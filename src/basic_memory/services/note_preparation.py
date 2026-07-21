"""Prepare canonical Markdown values without persistence side effects."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import (
    ParseError,
    dump_frontmatter,
    has_frontmatter,
    parse_frontmatter,
    remove_frontmatter,
)
from basic_memory.markdown import EntityMarkdown
from basic_memory.markdown.entity_parser import (
    EntityParser,
    _coerce_to_string,
    normalize_frontmatter_metadata,
)
from basic_memory.markdown.utils import schema_to_markdown
from basic_memory.models import Entity
from basic_memory.repository import AcceptedObservationWrite, AcceptedRelationWrite
from basic_memory.repository.entity_repository import EntityMetadata, EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.note_move import normalize_note_move_destination_path
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.base import Permalink
from basic_memory.services.exceptions import EntityAlreadyExistsError
from basic_memory.services.file_service import FileService
from basic_memory.utils import build_canonical_permalink
from basic_memory.workspace_context import workspace_slug_for_canonical_permalinks


@dataclass(frozen=True, slots=True)
class PreparedEntityFields:
    """Entity row values that mirror one accepted Markdown snapshot."""

    title: str
    note_type: str
    entity_metadata: EntityMetadata
    content_type: str
    permalink: str | None
    file_path: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PreparedEntityWrite:
    """Canonical Markdown and its parsed graph before persistence."""

    file_path: Path
    markdown_content: str
    search_content: str
    entity_fields: PreparedEntityFields
    entity_markdown: EntityMarkdown

    @property
    def observations(self) -> list[AcceptedObservationWrite]:
        return [
            AcceptedObservationWrite(
                content=observation.content,
                category=observation.category,
                context=observation.context,
                tags=observation.tags,
            )
            for observation in self.entity_markdown.observations
        ]

    @property
    def relations(self) -> list[AcceptedRelationWrite]:
        return [
            AcceptedRelationWrite(
                relation_type=relation.type,
                target_name=relation.target,
                context=relation.context,
            )
            for relation in self.entity_markdown.relations
        ]


@dataclass(frozen=True, slots=True)
class PreparedEntityMove:
    """Canonical Markdown and identity state for an accepted note move."""

    file_path: Path
    markdown_content: str
    search_content: str
    permalink: str | None


@dataclass(frozen=True, slots=True)
class PreparedEditTitleReconciliation:
    markdown_content: str
    title: str
    metadata: EntityMetadata


@dataclass(frozen=True, slots=True)
class NotePreparationDependencies:
    """Only the dependencies required to derive accepted note state."""

    entity_parser: EntityParser
    entity_repository: EntityRepository
    file_service: FileService
    session_maker: async_sessionmaker[AsyncSession]
    app_config: BasicMemoryConfig | None = None


def apply_prepared_entity_fields(
    entity: Entity,
    entity_fields: PreparedEntityFields,
    *,
    user_profile_value: str | None,
) -> None:
    """Copy prepared accepted Markdown fields onto an entity row."""
    entity.title = entity_fields.title
    entity.note_type = entity_fields.note_type
    entity.entity_metadata = entity_fields.entity_metadata
    entity.content_type = entity_fields.content_type
    entity.permalink = entity_fields.permalink
    entity.file_path = entity_fields.file_path
    entity.created_at = entity_fields.created_at
    entity.updated_at = entity_fields.updated_at
    entity.last_updated_by = user_profile_value


def _frontmatter_permalink(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _build_frontmatter_markdown(title: str, note_type: str, permalink: str) -> EntityMarkdown:
    from basic_memory.markdown.schemas import EntityFrontmatter

    return EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={"title": title, "type": note_type, "permalink": permalink}
        ),
        content="",
        observations=[],
        relations=[],
    )


async def detect_file_path_conflicts(
    dependencies: NotePreparationDependencies,
    file_path: str,
    *,
    skip_check: bool = False,
    session: AsyncSession | None = None,
) -> list[str]:
    if skip_check:
        return []

    from basic_memory.utils import detect_potential_file_conflicts

    async with db.scoped_session(dependencies.session_maker, session) as active_session:
        existing_paths = await dependencies.entity_repository.get_all_file_paths(active_session)
    return detect_potential_file_conflicts(file_path, existing_paths)


async def _project_permalink(
    dependencies: NotePreparationDependencies,
    session: AsyncSession,
) -> str | None:
    project_id = dependencies.entity_repository.project_id
    if project_id is None:  # pragma: no cover
        return None
    project = await ProjectRepository().get_by_id(session, project_id)
    return project.permalink if project else None


async def resolve_permalink(
    dependencies: NotePreparationDependencies,
    file_path: Permalink | Path,
    markdown: EntityMarkdown | None = None,
    *,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
) -> str:
    """Resolve the unique canonical permalink for one prepared note."""
    file_path_str = Path(file_path).as_posix()
    async with db.scoped_session(dependencies.session_maker, session) as active_session:
        conflicts = await detect_file_path_conflicts(
            dependencies,
            file_path_str,
            skip_check=skip_conflict_check,
            session=active_session,
        )
        if conflicts:
            logger.warning(
                f"Detected potential file path conflicts for '{file_path_str}': {conflicts}"
            )

        if markdown and markdown.frontmatter.permalink:
            desired_permalink = markdown.frontmatter.permalink
            existing_file_path = await dependencies.entity_repository.get_file_path_for_permalink(
                active_session, desired_permalink
            )
            if not existing_file_path or existing_file_path == file_path_str:
                return desired_permalink

        existing_permalink = await dependencies.entity_repository.get_permalink_for_file_path(
            active_session, file_path_str
        )
        if existing_permalink:
            return existing_permalink

        if markdown and markdown.frontmatter.permalink:
            desired_permalink = markdown.frontmatter.permalink
        else:
            include_project = (
                dependencies.app_config.permalinks_include_project
                if dependencies.app_config is not None
                else True
            )
            workspace_permalink = workspace_slug_for_canonical_permalinks()
            project_permalink = None
            if include_project or workspace_permalink:
                project_permalink = await _project_permalink(dependencies, active_session)
            desired_permalink = build_canonical_permalink(
                project_permalink,
                file_path_str,
                include_project=include_project,
                workspace_permalink=workspace_permalink,
            )

        permalink = desired_permalink
        suffix = 1
        while await dependencies.entity_repository.permalink_exists(active_session, permalink):
            permalink = f"{desired_permalink}-{suffix}"
            suffix += 1
    return permalink


def _apply_schema_frontmatter_overrides(schema: EntitySchema) -> EntityMarkdown | None:
    if not schema.content or not has_frontmatter(schema.content):
        return None
    content_frontmatter = parse_frontmatter(schema.content)
    if "type" in content_frontmatter:
        schema.note_type = _coerce_to_string(content_frontmatter["type"])
    content_permalink = _frontmatter_permalink(content_frontmatter.get("permalink"))
    if content_permalink is None:
        return None
    return _build_frontmatter_markdown(schema.title, schema.note_type, content_permalink)


async def _resolve_schema_permalink(
    dependencies: NotePreparationDependencies,
    schema: EntitySchema,
    *,
    file_path: Path,
    current_permalink: str | None = None,
    content_markdown: EntityMarkdown | None = None,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
) -> str | None:
    if dependencies.app_config and dependencies.app_config.disable_permalinks:
        schema._permalink = current_permalink or ""
        return current_permalink
    if current_permalink and not (content_markdown and content_markdown.frontmatter.permalink):
        schema._permalink = current_permalink
        return current_permalink
    resolved = await resolve_permalink(
        dependencies,
        file_path,
        content_markdown,
        skip_conflict_check=skip_conflict_check,
        session=session,
    )
    schema._permalink = resolved
    return resolved


def _build_entity_fields(
    *,
    file_path: Path,
    content_type: str,
    permalink: str | None,
    entity_markdown: EntityMarkdown,
) -> PreparedEntityFields:
    if entity_markdown.created is None or entity_markdown.modified is None:  # pragma: no cover
        raise ValueError("Prepared Markdown requires created and modified timestamps")

    normalized_metadata = normalize_frontmatter_metadata(entity_markdown.frontmatter.metadata or {})
    entity_metadata = {
        key: value for key, value in normalized_metadata.items() if value is not None
    }
    return PreparedEntityFields(
        title=entity_markdown.frontmatter.title,
        note_type=entity_markdown.frontmatter.type,
        entity_metadata=entity_metadata or None,
        content_type=content_type,
        permalink=permalink,
        file_path=file_path.as_posix(),
        created_at=entity_markdown.created,
        updated_at=entity_markdown.modified,
    )


async def _build_prepared_write(
    dependencies: NotePreparationDependencies,
    *,
    file_path: Path,
    markdown_content: str,
    content_type: str,
    permalink: str | None,
    preserved_created_at: datetime | None = None,
) -> PreparedEntityWrite:
    entity_markdown = await dependencies.entity_parser.parse_markdown_content(
        file_path=file_path,
        content=markdown_content,
        # DB-first updates have no file ctime. Preserve the semantic creation time
        # so editing a legacy note cannot make it appear newly created.
        ctime=(preserved_created_at.timestamp() if preserved_created_at is not None else None),
    )
    return PreparedEntityWrite(
        file_path=file_path,
        markdown_content=markdown_content,
        search_content=remove_frontmatter(markdown_content),
        entity_fields=_build_entity_fields(
            file_path=file_path,
            content_type=content_type,
            permalink=permalink,
            entity_markdown=entity_markdown,
        ),
        entity_markdown=entity_markdown,
    )


async def prepare_create_entity_content(
    dependencies: NotePreparationDependencies,
    schema: EntitySchema,
    *,
    check_storage_exists: bool = True,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
) -> PreparedEntityWrite:
    schema = schema.model_copy(deep=True)
    file_path = Path(schema.file_path)
    if check_storage_exists and await dependencies.file_service.exists(file_path):
        raise EntityAlreadyExistsError(
            f"file for entity {schema.directory}/{schema.title} already exists: {file_path}"
        )
    content_markdown = _apply_schema_frontmatter_overrides(schema)
    permalink = await _resolve_schema_permalink(
        dependencies,
        schema,
        file_path=file_path,
        content_markdown=content_markdown,
        skip_conflict_check=skip_conflict_check,
        session=session,
    )
    post = await schema_to_markdown(schema)
    markdown_content = dump_frontmatter(post)
    return await _build_prepared_write(
        dependencies,
        file_path=file_path,
        markdown_content=markdown_content,
        content_type=schema.content_type,
        permalink=permalink,
    )


async def prepare_update_entity_content(
    dependencies: NotePreparationDependencies,
    entity: Entity,
    schema: EntitySchema,
    existing_content: str,
    *,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
) -> PreparedEntityWrite:
    schema = schema.model_copy(deep=True)
    file_path = Path(schema.file_path)
    current_file_path = Path(entity.file_path)
    existing_metadata: dict[str, object] = {}
    if has_frontmatter(existing_content):
        try:
            existing_metadata = parse_frontmatter(existing_content)
        except ParseError:
            # A replacement may repair malformed existing frontmatter. Discard only
            # that invalid merge input; the final accepted Markdown is validated below.
            pass
    content_markdown = _apply_schema_frontmatter_overrides(schema)
    update_permalink_on_rename = bool(
        dependencies.app_config and dependencies.app_config.update_permalinks_on_move
    )
    current_permalink = (
        entity.permalink
        if file_path.as_posix() == current_file_path.as_posix() or not update_permalink_on_rename
        else None
    )
    resolved_permalink = await _resolve_schema_permalink(
        dependencies,
        schema,
        file_path=file_path,
        current_permalink=current_permalink,
        content_markdown=content_markdown,
        skip_conflict_check=skip_conflict_check,
        session=session,
    )
    post = await schema_to_markdown(schema)
    merged_metadata = deepcopy(existing_metadata)
    merged_metadata.update(post.metadata)
    merged_metadata["permalink"] = resolved_permalink
    merged_post = frontmatter.Post(post.content)
    merged_post.metadata.update(merged_metadata)
    markdown_content = dump_frontmatter(merged_post)
    return await _build_prepared_write(
        dependencies,
        file_path=file_path,
        markdown_content=markdown_content,
        content_type=schema.content_type,
        permalink=resolved_permalink,
        preserved_created_at=entity.created_at,
    )


def reconcile_prepared_edit_title_from_h1(
    *,
    original_markdown: str,
    markdown_content: str,
    current_title: str | None,
    prepared_title: str,
    metadata: EntityMetadata,
) -> PreparedEditTitleReconciliation:
    from basic_memory.indexing.accepted_note_search import (
        accepted_search_content_from_markdown,
        first_markdown_h1,
    )

    original_h1 = first_markdown_h1(accepted_search_content_from_markdown(original_markdown))
    prepared_h1 = first_markdown_h1(accepted_search_content_from_markdown(markdown_content))
    if (
        not prepared_h1
        or prepared_h1 == prepared_title
        or original_h1 != current_title
        or prepared_title != current_title
    ):
        return PreparedEditTitleReconciliation(markdown_content, prepared_title, metadata)
    post = frontmatter.loads(markdown_content)
    post.metadata["title"] = prepared_h1
    reconciled_metadata = {**metadata, "title": prepared_h1} if metadata is not None else None
    return PreparedEditTitleReconciliation(dump_frontmatter(post), prepared_h1, reconciled_metadata)


def _markdown_heading_level(line: str) -> int | None:
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate.startswith("#"):
        return None
    level = len(candidate) - len(candidate.lstrip("#"))
    if level > 6:
        return None
    rest = candidate[level:]
    return level if not rest or rest.startswith((" ", "\t")) else None


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate or candidate[0] not in ("`", "~"):
        return None
    marker = candidate[0]
    marker_length = len(candidate) - len(candidate.lstrip(marker))
    if marker_length < 3:
        return None
    return marker, marker_length, candidate[marker_length:]


def _fenced_code_line_flags(lines: list[str]) -> list[bool]:
    flags: list[bool] = []
    open_marker: str | None = None
    open_length = 0
    for line in lines:
        marker = _fence_marker(line)
        if open_marker is None:
            if marker is None:
                flags.append(False)
                continue
            marker_char, marker_length, suffix = marker
            if marker_char == "`" and "`" in suffix:
                flags.append(False)
                continue
            flags.append(True)
            open_marker = marker_char
            open_length = marker_length
            continue
        flags.append(True)
        if marker is not None:
            marker_char, marker_length, suffix = marker
            if marker_char == open_marker and marker_length >= open_length and not suffix.strip():
                open_marker = None
                open_length = 0
    return flags


def replace_section_content(
    current_content: str,
    section_header: str,
    new_content: str,
    replace_subsections: bool = True,
) -> str:
    if not section_header.startswith("#"):
        section_header = "## " + section_header
    new_content_lines = new_content.lstrip().split("\n")
    if new_content_lines and new_content_lines[0].strip() == section_header.strip():
        new_content = "\n".join(new_content_lines[1:]).lstrip()
    lines = current_content.split("\n")
    fenced = _fenced_code_line_flags(lines)
    matches = [
        index
        for index, line in enumerate(lines)
        if not fenced[index] and line.strip() == section_header.strip()
    ]
    if len(matches) > 1:
        raise ValueError(
            f"Multiple sections found with header '{section_header}'. "
            "Section replacement requires unique headers."
        )
    if not matches:
        logger.info(f"Section '{section_header}' not found, appending to end of document")
        separator = "\n\n" if current_content and not current_content.endswith("\n\n") else ""
        return current_content + separator + section_header + "\n" + new_content
    section_line_index = matches[0]
    target_level = len(section_header) - len(section_header.lstrip("#"))
    end_index = len(lines)
    for index in range(section_line_index + 1, len(lines)):
        if fenced[index]:
            continue
        heading_level = _markdown_heading_level(lines[index])
        if heading_level is not None and (not replace_subsections or heading_level <= target_level):
            end_index = index
            break
    return "\n".join([*lines[: section_line_index + 1], new_content, *lines[end_index:]])


def insert_relative_to_section(
    current_content: str,
    section_header: str,
    new_content: str,
    position: str,
) -> str:
    if not section_header.startswith("#"):
        section_header = "## " + section_header
    lines = current_content.split("\n")
    fenced = _fenced_code_line_flags(lines)
    matches = [
        index
        for index, line in enumerate(lines)
        if not fenced[index] and line.strip() == section_header.strip()
    ]
    if not matches:
        raise ValueError(
            f"Section '{section_header}' not found in document. "
            "Use replace_section to create a new section."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple sections found with header '{section_header}'. "
            "Section insertion requires unique headers."
        )
    index = matches[0]
    insert_lines = new_content.rstrip("\n").split("\n")
    if position == "before":
        before = lines[:index]
        if before and before[-1].strip():
            insert_lines = ["", *insert_lines]
        return "\n".join([*before, *insert_lines, "", *lines[index:]])
    after = lines[index + 1 :]
    if after and after[0].strip():
        insert_lines.append("")
    return "\n".join([*lines[: index + 1], *insert_lines, *after])


def _prepend_after_frontmatter(current_content: str, content: str) -> str:
    if has_frontmatter(current_content):
        frontmatter_data = parse_frontmatter(current_content)
        body_content = remove_frontmatter(current_content)
        new_body = content + ("\n" if content and not content.endswith("\n") else "")
        new_body += body_content
        yaml_frontmatter = yaml.dump(frontmatter_data, sort_keys=False, allow_unicode=True)
        return f"---\n{yaml_frontmatter}---\n\n{new_body.strip()}"
    return content + ("\n" if content and not content.endswith("\n") else "") + current_content


def apply_edit_operation(
    current_content: str,
    operation: str,
    content: str,
    section: str | None = None,
    find_text: str | None = None,
    expected_replacements: int = 1,
    replace_subsections: bool = True,
) -> str:
    if operation == "append":
        return (
            current_content
            + ("\n" if current_content and not current_content.endswith("\n") else "")
            + content
        )
    if operation == "prepend":
        return _prepend_after_frontmatter(current_content, content)
    if operation == "find_replace":
        if not find_text:
            raise ValueError("find_text is required for find_replace operation")
        if not find_text.strip():
            raise ValueError("find_text cannot be empty or whitespace only")
        actual_count = current_content.count(find_text)
        if actual_count != expected_replacements:
            if actual_count == 0:
                raise ValueError(f"Text to replace not found: '{find_text}'")
            raise ValueError(
                f"Expected {expected_replacements} occurrences of '{find_text}', "
                f"but found {actual_count}"
            )
        return current_content.replace(find_text, content)
    if operation == "replace_section":
        if not section:
            raise ValueError("section is required for replace_section operation")
        if not section.strip():
            raise ValueError("section cannot be empty or whitespace only")
        return replace_section_content(
            current_content, section, content, replace_subsections=replace_subsections
        )
    if operation in ("insert_before_section", "insert_after_section"):
        if not section:
            raise ValueError("section is required for insert section operations")
        if not section.strip():
            raise ValueError("section cannot be empty or whitespace only")
        position = "before" if operation == "insert_before_section" else "after"
        return insert_relative_to_section(current_content, section, content, position)
    raise ValueError(f"Unsupported operation: {operation}")


# title/type/permalink already have dedicated resolution paths in
# prepare_edit_entity_content (H1 title reconciliation, permalink resolver). Letting a
# metadata merge touch them would race with those paths and could be silently reverted.
_METADATA_IDENTITY_FIELDS = frozenset({"title", "type", "permalink"})


def _merge_metadata_into_markdown(markdown_content: str, metadata: dict[str, Any]) -> str:
    """Merge caller-supplied fields into a markdown string's YAML frontmatter.

    Identity fields (title/type/permalink) are dropped from the merge; every other key
    overwrites the existing frontmatter value or is added new. The note body, and any
    frontmatter keys not present in ``metadata``, are left untouched.
    """
    null_keys = sorted(key for key, value in metadata.items() if value is None)
    if null_keys:
        # A null value would be filtered out of the indexed entity metadata right after
        # this merge, silently losing the field even though key deletion is unsupported.
        raise ValueError(
            "metadata values cannot be null (key deletion is not supported): "
            + ", ".join(null_keys)
        )
    sanitized = {k: v for k, v in metadata.items() if k not in _METADATA_IDENTITY_FIELDS}
    if not sanitized:
        return markdown_content

    had_separator = True
    if has_frontmatter(markdown_content):
        current_metadata = parse_frontmatter(markdown_content)
        # strip=False: a frontmatter-only rewrite must not reflow the body (leading
        # blank lines, trailing hard-break spaces). dump_frontmatter re-inserts the
        # single blank separator line after the closing fence, so drop exactly one
        # leading newline here to round-trip the body unchanged.
        body = remove_frontmatter(markdown_content, strip=False)
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        else:
            had_separator = False
    else:
        current_metadata = {}
        body = markdown_content

    merged_metadata = deepcopy(current_metadata)
    merged_metadata.update(sanitized)

    post = frontmatter.Post(body)
    post.metadata.update(merged_metadata)
    if not had_separator and body:
        # Trigger: the original note had no blank line between the closing fence and
        # the body ("---\nBody"), but dump_frontmatter always emits one.
        # Outcome: serialize the frontmatter alone and reattach the body verbatim so
        # a frontmatter-only merge cannot insert a blank line into the body.
        post.content = ""
        return dump_frontmatter(post) + body
    return dump_frontmatter(post)


async def prepare_edit_entity_content(
    dependencies: NotePreparationDependencies,
    entity: Entity,
    current_content: str,
    *,
    operation: str,
    content: str,
    section: str | None = None,
    find_text: str | None = None,
    expected_replacements: int = 1,
    replace_subsections: bool = True,
    metadata: dict[str, Any] | None = None,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
) -> PreparedEntityWrite:
    file_path = Path(entity.file_path)
    # Trigger: the documented metadata-only pattern is empty append/prepend plus metadata.
    # Why: an empty append still appends "\n" to a body without a trailing newline, so a
    # request advertised as frontmatter-only would mutate the body it promised to keep.
    # Outcome: skip the content operation entirely and merge into the current text.
    metadata_only_edit = bool(metadata) and operation in ("append", "prepend") and not content
    if metadata_only_edit:
        markdown_content = current_content
    else:
        markdown_content = apply_edit_operation(
            current_content,
            operation,
            content,
            section,
            find_text,
            expected_replacements,
            replace_subsections,
        )
    # Merge before frontmatter-derived resolution below so merged keys land in the
    # indexed entity metadata in the same pass — see _merge_metadata_into_markdown.
    if metadata:
        markdown_content = _merge_metadata_into_markdown(markdown_content, metadata)
    title = entity.title
    note_type = entity.note_type
    permalink = entity.permalink
    metadata = entity.entity_metadata
    if has_frontmatter(markdown_content):
        content_frontmatter = parse_frontmatter(markdown_content)
        if "title" in content_frontmatter:
            title = _coerce_to_string(content_frontmatter["title"])
        if "type" in content_frontmatter:
            note_type = _coerce_to_string(content_frontmatter["type"])
        if dependencies.app_config and dependencies.app_config.disable_permalinks:
            permalink = entity.permalink
        else:
            content_permalink = _frontmatter_permalink(content_frontmatter.get("permalink"))
            if content_permalink is not None:
                permalink = await resolve_permalink(
                    dependencies,
                    file_path,
                    _build_frontmatter_markdown(title, note_type, content_permalink),
                    skip_conflict_check=skip_conflict_check,
                    session=session,
                )
        normalized_metadata = normalize_frontmatter_metadata(content_frontmatter or {})
        metadata = {
            key: value for key, value in normalized_metadata.items() if value is not None
        } or None
    reconciliation = reconcile_prepared_edit_title_from_h1(
        original_markdown=current_content,
        markdown_content=markdown_content,
        current_title=entity.title,
        prepared_title=title,
        metadata=metadata,
    )
    return await _build_prepared_write(
        dependencies,
        file_path=file_path,
        markdown_content=reconciliation.markdown_content,
        content_type=entity.content_type,
        permalink=permalink,
        preserved_created_at=entity.created_at,
    )


async def prepare_move_entity_content(
    dependencies: NotePreparationDependencies,
    entity: Entity,
    current_content: str,
    destination_path: str,
    *,
    session: AsyncSession | None = None,
) -> PreparedEntityMove:
    from basic_memory.indexing.accepted_note_search import accepted_search_content_from_markdown

    file_path = Path(normalize_note_move_destination_path(destination_path))
    markdown_content = current_content
    permalink = entity.permalink
    disable_permalinks = bool(
        dependencies.app_config and dependencies.app_config.disable_permalinks
    )
    update_permalinks_on_move = bool(
        dependencies.app_config and dependencies.app_config.update_permalinks_on_move
    )
    if not disable_permalinks and (update_permalinks_on_move or entity.permalink is None):
        permalink = await resolve_permalink(dependencies, file_path, session=session)
        post = frontmatter.loads(markdown_content)
        post.metadata["permalink"] = permalink
        markdown_content = dump_frontmatter(post)
    return PreparedEntityMove(
        file_path=file_path,
        markdown_content=markdown_content,
        search_content=accepted_search_content_from_markdown(markdown_content),
        permalink=permalink,
    )


def paths_share_storage_target(file_service: FileService, left: Path, right: Path) -> bool:
    left_path = file_service.base_path / left
    right_path = file_service.base_path / right
    if not left_path.exists() or not right_path.exists():
        return False
    try:
        return left_path.samefile(right_path)
    except OSError:
        return False


async def verify_move_destination_absent(
    dependencies: NotePreparationDependencies,
    *,
    source_file_path: str,
    destination_file_path: str,
) -> None:
    source = Path(source_file_path)
    destination = Path(normalize_note_move_destination_path(destination_file_path))
    if (
        source != destination
        and await dependencies.file_service.exists(destination)
        and not paths_share_storage_target(dependencies.file_service, source, destination)
    ):
        raise EntityAlreadyExistsError(
            f"file already exists at destination path: {destination.as_posix()}"
        )


async def resolve_deferred_self_relation(
    dependencies: NotePreparationDependencies,
    target: str,
    entity: Entity,
    session: AsyncSession | None = None,
) -> Entity | None:
    clean_target = target.strip()
    if clean_target.startswith("[[") and clean_target.endswith("]]"):
        clean_target = clean_target[2:-2].strip()
    if "|" in clean_target:
        clean_target = clean_target.split("|", 1)[0].strip()
    candidates = {entity.file_path}
    if entity.permalink:
        candidates.add(entity.permalink)
    if entity.file_path.endswith(".md"):
        candidates.add(entity.file_path[:-3])
    if clean_target in candidates:
        return entity
    if clean_target != entity.title:
        return None
    async with db.scoped_session(dependencies.session_maker, session) as active_session:
        matches = await dependencies.entity_repository.get_by_title(
            active_session, clean_target, load_relations=False
        )
    return entity if len(matches) == 1 and matches[0].id == entity.id else None


@dataclass(frozen=True, slots=True)
class NotePreparation:
    """Method-shaped adapter for accepted-note preparation protocols."""

    dependencies: NotePreparationDependencies

    async def detect_file_path_conflicts(
        self, file_path: str, skip_check: bool = False, session: AsyncSession | None = None
    ) -> list[str]:
        return await detect_file_path_conflicts(
            self.dependencies, file_path, skip_check=skip_check, session=session
        )

    async def resolve_permalink(
        self,
        file_path: Permalink | Path,
        markdown: EntityMarkdown | None = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> str:
        return await resolve_permalink(
            self.dependencies,
            file_path,
            markdown,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        return await prepare_create_entity_content(
            self.dependencies,
            schema,
            check_storage_exists=check_storage_exists,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        return await prepare_update_entity_content(
            self.dependencies,
            entity,
            schema,
            existing_content,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_edit_entity_content(
        self,
        entity: Entity,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: str | None = None,
        find_text: str | None = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
        metadata: dict[str, Any] | None = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        return await prepare_edit_entity_content(
            self.dependencies,
            entity,
            current_content,
            operation=operation,
            content=content,
            section=section,
            find_text=find_text,
            expected_replacements=expected_replacements,
            replace_subsections=replace_subsections,
            metadata=metadata,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_move_entity_content(
        self,
        entity: Entity,
        current_content: str,
        destination_path: str,
        *,
        session: AsyncSession | None = None,
    ) -> PreparedEntityMove:
        return await prepare_move_entity_content(
            self.dependencies,
            entity,
            current_content,
            destination_path,
            session=session,
        )

    async def verify_move_destination_absent(
        self, *, source_file_path: str, destination_file_path: str
    ) -> None:
        await verify_move_destination_absent(
            self.dependencies,
            source_file_path=source_file_path,
            destination_file_path=destination_file_path,
        )

    async def resolve_deferred_self_relation(
        self, target: str, entity: Entity, session: AsyncSession | None = None
    ) -> Entity | None:
        return await resolve_deferred_self_relation(
            self.dependencies, target, entity, session=session
        )
