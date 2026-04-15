"""Service for managing entities in the database."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

import frontmatter
import yaml
from loguru import logger
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.file_utils import (
    has_frontmatter,
    parse_frontmatter,
    remove_frontmatter,
    dump_frontmatter,
)
from basic_memory.markdown import EntityMarkdown
from basic_memory.markdown.entity_parser import (
    EntityParser,
    _coerce_to_string,
    normalize_frontmatter_metadata,
)
from basic_memory.markdown.utils import entity_model_from_markdown, schema_to_markdown
from basic_memory.models import Entity as EntityModel
from basic_memory.models import Observation, Relation
from basic_memory.models.knowledge import Entity
from basic_memory.repository import ObservationRepository, RelationRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.base import Permalink
from basic_memory.schemas.response import (
    DirectoryMoveResult,
    DirectoryMoveError,
    DirectoryDeleteResult,
    DirectoryDeleteError,
)
from basic_memory.services import BaseService, FileService
from basic_memory.services.exceptions import (
    EntityAlreadyExistsError,
    EntityCreationError,
    EntityNotFoundError,
)
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.utils import build_canonical_permalink


@dataclass(frozen=True)
class EntityWriteResult:
    """Persisted entity plus the response/search content produced during this call."""

    entity: EntityModel
    content: str
    search_content: str


@dataclass(frozen=True)
class PreparedEntityWrite:
    """Accepted note state before any persistence side effects happen.

    Prepare methods return this object after all note semantics have been resolved,
    but before any file writes or database mutations occur.

    Attributes:
        file_path: Canonical note path implied by the request.
        markdown_content: Full markdown to persist, including frontmatter.
        search_content: Frontmatter-stripped content for inline FTS indexing.
        entity_fields: Entity row values that mirror the accepted markdown state.
            Keys are ``title``, ``note_type``, ``file_path``, ``content_type``,
            ``entity_metadata``, and ``permalink``.
        entity_markdown: Parsed markdown reused by the local write path to update
            entities, observations, and relations without reparsing a second time.
    """

    file_path: Path
    markdown_content: str
    search_content: str
    entity_fields: dict[str, Any]
    entity_markdown: EntityMarkdown


def _frontmatter_permalink(value: object) -> str | None:
    """Return an explicit frontmatter permalink only when YAML parsed a real string."""
    return value if isinstance(value, str) and value else None


class EntityService(BaseService[EntityModel]):
    """Service for managing entities in the database."""

    def __init__(
        self,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        file_service: FileService,
        link_resolver: LinkResolver,
        search_service: Optional[SearchService] = None,
        app_config: Optional[BasicMemoryConfig] = None,
    ):
        super().__init__(entity_repository)
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.entity_parser = entity_parser
        self.file_service = file_service
        self.link_resolver = link_resolver
        self.search_service = search_service
        self.app_config = app_config
        self._project_permalink: Optional[str] = None
        # Callable that returns the current user ID (cloud user_profile_id UUID as string).
        # Default returns None for local/CLI usage. Cloud overrides this to read from UserContext.
        self.get_user_id: Callable[[], Optional[str]] = lambda: None

    async def detect_file_path_conflicts(
        self, file_path: str, skip_check: bool = False
    ) -> List[str]:
        """Detect potential file path conflicts for a given file path.

        This checks for entities with similar file paths that might cause conflicts:
        - Case sensitivity differences (Finance/file.md vs finance/file.md)
        - Character encoding differences
        - Hyphen vs space differences
        - Unicode normalization differences

        Args:
            file_path: The file path to check for conflicts
            skip_check: If True, skip the check and return empty list (optimization for bulk operations)

        Returns:
            List of file paths that might conflict with the given file path
        """
        if skip_check:
            return []

        from basic_memory.utils import detect_potential_file_conflicts

        # Load only file paths. Conflict detection is on the hot write path and
        # does not need observations or relations.
        existing_paths = await self.repository.get_all_file_paths()

        # Use the enhanced conflict detection utility
        return detect_potential_file_conflicts(file_path, existing_paths)

    async def resolve_permalink(
        self,
        file_path: Permalink | Path,
        markdown: Optional[EntityMarkdown] = None,
        skip_conflict_check: bool = False,
    ) -> str:
        """Get or generate unique permalink for an entity.

        Priority:
        1. If markdown has permalink and it's not used by another file -> use as is
        2. If markdown has permalink but it's used by another file -> make unique
        3. For existing files, keep current permalink from db
        4. Generate new unique permalink from file path

        Enhanced to detect and handle character-related conflicts.

        Note: Uses lightweight repository methods that skip eager loading of
        observations and relations for better performance during bulk operations.
        """
        file_path_str = Path(file_path).as_posix()

        # Check for potential file path conflicts before resolving permalink
        conflicts = await self.detect_file_path_conflicts(
            file_path_str, skip_check=skip_conflict_check
        )
        if conflicts:
            logger.warning(
                f"Detected potential file path conflicts for '{file_path_str}': {conflicts}"
            )

        # If markdown has explicit permalink, try to validate it
        if markdown and markdown.frontmatter.permalink:
            desired_permalink = markdown.frontmatter.permalink
            # Use lightweight method - we only need to check file_path
            existing_file_path = await self.repository.get_file_path_for_permalink(
                desired_permalink
            )

            # If no conflict or it's our own file, use as is
            if not existing_file_path or existing_file_path == file_path_str:
                return desired_permalink

        # For existing files, try to find current permalink
        # Use lightweight method - we only need the permalink
        existing_permalink = await self.repository.get_permalink_for_file_path(file_path_str)
        if existing_permalink:
            return existing_permalink

        # New file - generate permalink
        if markdown and markdown.frontmatter.permalink:
            desired_permalink = markdown.frontmatter.permalink
        else:
            # Trigger: generating a permalink for a new file
            # Why: canonical permalinks may require project prefix for global addressing
            # Outcome: include project slug when enabled in config
            include_project = True
            if self.app_config:
                include_project = self.app_config.permalinks_include_project

            project_permalink = None
            # Trigger: project-prefixed permalinks are enabled
            # Why: we need the project slug to build the canonical permalink
            # Outcome: fetch and cache the project's permalink
            if include_project:
                project_permalink = await self._get_project_permalink()

            desired_permalink = build_canonical_permalink(
                project_permalink, file_path_str, include_project=include_project
            )

        # Make unique if needed - enhanced to handle character conflicts
        # Use lightweight existence check instead of loading full entity
        permalink = desired_permalink
        suffix = 1
        while await self.repository.permalink_exists(permalink):
            permalink = f"{desired_permalink}-{suffix}"
            suffix += 1
            logger.debug(f"creating unique permalink: {permalink}")

        return permalink

    async def _get_project_permalink(self) -> Optional[str]:
        """Get and cache the current project's permalink."""
        if self._project_permalink is not None:
            return self._project_permalink

        project_id = self.repository.project_id
        if project_id is None:  # pragma: no cover
            return None  # pragma: no cover

        project_repository = ProjectRepository(self.repository.session_maker)
        project = await project_repository.get_by_id(project_id)
        if project:
            self._project_permalink = project.permalink
        return self._project_permalink

    def _build_frontmatter_markdown(
        self, title: str, note_type: str, permalink: str
    ) -> EntityMarkdown:
        """Build a minimal EntityMarkdown object for permalink resolution."""
        from basic_memory.markdown.schemas import EntityFrontmatter

        frontmatter_metadata = {
            "title": title,
            "type": note_type,
            "permalink": permalink,
        }
        frontmatter_obj = EntityFrontmatter(metadata=frontmatter_metadata)
        return EntityMarkdown(
            frontmatter=frontmatter_obj,
            content="",
            observations=[],
            relations=[],
        )

    def _coerce_schema_input(self, schema: EntitySchema | EntityModel) -> EntitySchema:
        """Normalize legacy Entity-like inputs into the schema shape prepare methods expect."""
        if isinstance(schema, EntitySchema):
            return schema

        # create_or_update_entity historically tolerated callers passing an ORM entity that had
        # been annotated with ad-hoc content. Preserve that compatibility at the wrapper boundary
        # so the prepare layer itself can stay strict and schema-focused.
        directory = Path(schema.file_path).parent.as_posix()
        normalized = EntitySchema(
            title=schema.title,
            content=getattr(schema, "content", None),
            directory="" if directory == "." else directory,
            note_type=schema.note_type,
            entity_metadata=schema.entity_metadata,
            content_type=schema.content_type,
        )
        normalized._permalink = schema.permalink
        return normalized

    def _sync_prepared_schema_state(
        self,
        source_schema: EntitySchema | EntityModel,
        prepared: PreparedEntityWrite,
    ) -> None:
        """Preserve the legacy side effect where write helpers populate the caller's schema."""
        if not isinstance(source_schema, EntitySchema):
            return

        # Older service flows mutated the request schema with the resolved permalink and any
        # frontmatter-derived note type. Several callers and tests still rely on that behavior
        # after create/update returns.
        source_schema.title = prepared.entity_fields["title"]
        source_schema.note_type = prepared.entity_fields["note_type"]
        source_schema.content_type = prepared.entity_fields["content_type"]
        source_schema.entity_metadata = prepared.entity_fields["entity_metadata"]

        if self.app_config and self.app_config.disable_permalinks:
            source_schema._permalink = ""
        else:
            source_schema._permalink = prepared.entity_fields["permalink"]

    def _apply_schema_frontmatter_overrides(self, schema: EntitySchema) -> EntityMarkdown | None:
        """Apply schema content frontmatter overrides and return permalink resolution metadata."""
        if not schema.content or not has_frontmatter(schema.content):
            return None

        # Trigger: callers supply markdown that already contains frontmatter.
        # Why: user-authored frontmatter is part of accepted note semantics, not a persistence detail.
        # Outcome: note_type/permalink derivation happens before any write path decides how to persist.
        content_frontmatter = parse_frontmatter(schema.content)

        if "type" in content_frontmatter:
            schema.note_type = _coerce_to_string(content_frontmatter["type"])

        if "permalink" not in content_frontmatter:
            return None

        content_permalink = _frontmatter_permalink(content_frontmatter["permalink"])
        if content_permalink is None:
            return None

        return self._build_frontmatter_markdown(
            schema.title,
            schema.note_type,
            content_permalink,
        )

    async def _resolve_schema_permalink(
        self,
        schema: EntitySchema,
        *,
        file_path: Path,
        current_permalink: str | None = None,
        content_markdown: EntityMarkdown | None = None,
        skip_conflict_check: bool = False,
    ) -> str | None:
        """Resolve the canonical permalink for a create/update write."""
        if self.app_config and self.app_config.disable_permalinks:
            if current_permalink is None:
                schema._permalink = ""
                return None
            schema._permalink = current_permalink
            return current_permalink

        if current_permalink and not (content_markdown and content_markdown.frontmatter.permalink):
            schema._permalink = current_permalink
            return current_permalink

        resolved_permalink = await self.resolve_permalink(
            file_path,
            content_markdown,
            skip_conflict_check=skip_conflict_check,
        )
        schema._permalink = resolved_permalink
        return resolved_permalink

    def _build_entity_fields(
        self,
        *,
        file_path: Path,
        title: str,
        note_type: str,
        content_type: str,
        metadata: dict[str, Any] | None,
        permalink: str | None,
    ) -> dict[str, Any]:
        """Build the entity row data that mirrors accepted markdown state."""
        normalized_metadata = normalize_frontmatter_metadata(metadata or {})
        entity_metadata = {k: v for k, v in normalized_metadata.items() if v is not None}
        return {
            "title": title,
            "note_type": note_type,
            "file_path": file_path.as_posix(),
            "content_type": content_type,
            "entity_metadata": entity_metadata or None,
            "permalink": permalink,
        }

    async def _build_prepared_write(
        self,
        *,
        file_path: Path,
        markdown_content: str,
        entity_fields: dict[str, Any],
    ) -> PreparedEntityWrite:
        """Parse accepted markdown once so all persistence paths share the same state."""
        # Trigger: both local and cloud-style callers need the exact same accepted markdown.
        # Why: parsing twice creates opportunities for drift between "what we accepted" and
        #      "what we indexed/persisted".
        # Outcome: callers carry one prepared object through file writes, DB writes, and indexing.
        entity_markdown = await self.entity_parser.parse_markdown_content(
            file_path=file_path,
            content=markdown_content,
        )
        return PreparedEntityWrite(
            file_path=file_path,
            markdown_content=markdown_content,
            search_content=remove_frontmatter(markdown_content),
            entity_fields=entity_fields,
            entity_markdown=entity_markdown,
        )

    async def _read_persisted_write_content(self, file_path: Path) -> tuple[str, str]:
        """Read the stored markdown after write-time formatting has finished."""
        # Trigger: format-on-save or platform-specific text writes can change the stored markdown
        # after prepare accepted the request.
        # Why: API responses and inline search indexing should describe the note that actually
        #      landed on disk, not the pre-write snapshot.
        # Outcome: write helpers return persisted markdown plus search content derived from it.
        persisted_content = await self.file_service.read_file_content(file_path)
        return persisted_content, remove_frontmatter(persisted_content)

    def _paths_share_storage_target(self, left: Path, right: Path) -> bool:
        """Return whether two relative project paths point at the same stored file."""
        left_abs_path = self.file_service.base_path / left
        right_abs_path = self.file_service.base_path / right
        if not left_abs_path.exists() or not right_abs_path.exists():
            return False
        try:
            return left_abs_path.samefile(right_abs_path)
        except OSError:
            return False

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        skip_conflict_check: bool = False,
    ) -> PreparedEntityWrite:
        """Derive accepted markdown and entity fields for a new note.

        This is a public prepare step: it resolves frontmatter overrides,
        permalink semantics, and the final markdown body, but it does not write
        files or mutate database rows.

        Storage touch points:
            - When ``check_storage_exists`` is ``True`` (the default), this method
              calls ``file_service.exists(file_path)`` and raises
              ``EntityAlreadyExistsError`` if the target already exists.
            - When ``check_storage_exists`` is ``False``, callers opt into DB-first
              acceptance and must perform any external storage conflict handling
              themselves.
        """
        # Work on a copy so prepare methods are pure from the caller's perspective.
        # The router/service layer still receives the same accepted result, but we avoid mutating
        # the original schema instance in surprising ways.
        schema = schema.model_copy(deep=True)
        file_path = Path(schema.file_path)

        if check_storage_exists and await self.file_service.exists(file_path):
            raise EntityAlreadyExistsError(
                f"file for entity {schema.directory}/{schema.title} already exists: {file_path}"
            )

        content_markdown = self._apply_schema_frontmatter_overrides(schema)
        permalink = await self._resolve_schema_permalink(
            schema,
            file_path=file_path,
            content_markdown=content_markdown,
            skip_conflict_check=skip_conflict_check,
        )

        # Build the final markdown once here. Local mode will write it immediately; cloud mode can
        # store it in note_content first and materialize later without re-deriving anything.
        post = await schema_to_markdown(schema)
        markdown_content = dump_frontmatter(post)
        entity_fields = self._build_entity_fields(
            file_path=file_path,
            title=schema.title,
            note_type=schema.note_type,
            content_type=schema.content_type,
            metadata=post.metadata,
            permalink=permalink,
        )
        return await self._build_prepared_write(
            file_path=file_path,
            markdown_content=markdown_content,
            entity_fields=entity_fields,
        )

    async def prepare_update_entity_content(
        self,
        entity: EntityModel,
        schema: EntitySchema,
        existing_content: str,
        *,
        skip_conflict_check: bool = False,
    ) -> PreparedEntityWrite:
        """Derive accepted markdown and entity fields for a full note replacement.

        This method does not read or write storage on its own. The caller must
        supply ``existing_content`` for the current note body because full updates
        preserve unrecognized frontmatter keys from that explicit base content.
        No database rows are mutated here.
        """
        schema = schema.model_copy(deep=True)
        file_path = Path(schema.file_path)
        current_file_path = Path(entity.file_path)
        existing_markdown = await self.entity_parser.parse_markdown_content(
            file_path=current_file_path,
            content=existing_content,
        )

        content_markdown = self._apply_schema_frontmatter_overrides(schema)
        # Trigger: a full replacement may also rename the note by changing title or directory.
        # Why: cloud accepts the final markdown before S3 is updated, so the prepare contract must
        #      describe the requested destination instead of silently keeping the old path.
        # Outcome: unchanged paths preserve the current permalink; renamed paths only rotate the
        #          permalink when move-policy allows it or frontmatter explicitly sets one.
        update_permalink_on_rename = bool(
            self.app_config and self.app_config.update_permalinks_on_move
        )
        current_permalink = (
            entity.permalink
            if file_path.as_posix() == current_file_path.as_posix()
            or not update_permalink_on_rename
            else None
        )
        resolved_permalink = await self._resolve_schema_permalink(
            schema,
            file_path=file_path,
            current_permalink=current_permalink,
            content_markdown=content_markdown,
            skip_conflict_check=skip_conflict_check,
        )

        post = await schema_to_markdown(schema)

        # Full updates preserve unrecognized frontmatter keys from the existing note.
        # That keeps Basic Memory's write semantics stable for hand-authored metadata while still
        # letting the incoming schema replace the fields it explicitly owns.
        merged_metadata = deepcopy(existing_markdown.frontmatter.metadata)
        merged_metadata.update(post.metadata)
        merged_metadata["permalink"] = resolved_permalink

        merged_post = frontmatter.Post(post.content)
        merged_post.metadata.update(merged_metadata)

        markdown_content = dump_frontmatter(merged_post)
        entity_fields = self._build_entity_fields(
            file_path=file_path,
            title=schema.title,
            note_type=schema.note_type,
            content_type=schema.content_type,
            metadata=merged_post.metadata,
            permalink=resolved_permalink,
        )
        return await self._build_prepared_write(
            file_path=file_path,
            markdown_content=markdown_content,
            entity_fields=entity_fields,
        )

    async def prepare_edit_entity_content(
        self,
        entity: EntityModel,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
        skip_conflict_check: bool = False,
    ) -> PreparedEntityWrite:
        """Derive accepted markdown and entity fields for an edit request.

        This method operates only on the caller-provided ``current_content``. It
        does not read files, write files, or mutate database rows. That makes the
        edit base explicit so higher layers can reject stale content instead of
        silently editing whichever storage copy happens to be newest.
        """
        file_path = Path(entity.file_path)
        # Edits are intentionally based on explicit caller-supplied content. That makes stale-base
        # handling visible to the caller instead of quietly reading whatever persistence layer
        # happens to be newest.
        markdown_content = self.apply_edit_operation(
            current_content,
            operation,
            content,
            section,
            find_text,
            expected_replacements,
        )

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

            if self.app_config and self.app_config.disable_permalinks:
                permalink = entity.permalink
            elif "permalink" in content_frontmatter:
                content_permalink = _frontmatter_permalink(content_frontmatter["permalink"])
                if content_permalink is not None:
                    content_markdown = self._build_frontmatter_markdown(
                        title,
                        note_type,
                        content_permalink,
                    )
                    permalink = await self.resolve_permalink(
                        file_path,
                        content_markdown,
                        skip_conflict_check=skip_conflict_check,
                    )

            normalized_metadata = normalize_frontmatter_metadata(content_frontmatter or {})
            metadata = {k: v for k, v in normalized_metadata.items() if v is not None} or None

        entity_fields = self._build_entity_fields(
            file_path=file_path,
            title=title,
            note_type=note_type,
            content_type=entity.content_type,
            metadata=metadata,
            permalink=permalink,
        )
        return await self._build_prepared_write(
            file_path=file_path,
            markdown_content=markdown_content,
            entity_fields=entity_fields,
        )

    async def create_or_update_entity(self, schema: EntitySchema) -> Tuple[EntityModel, bool]:
        """Create new entity or update existing one.
        Returns: (entity, is_new) where is_new is True if a new entity was created
        """
        logger.debug(
            f"Creating or updating entity: {schema.file_path}, permalink: {schema.permalink}"
        )

        # Try to find existing entity using strict resolution (no fuzzy search)
        # This prevents incorrectly matching similar file paths like "Node A.md" and "Node C.md"
        existing = await self.link_resolver.resolve_link(
            schema.file_path,
            strict=True,
            load_relations=False,
        )
        if not existing and schema.permalink:
            existing = await self.link_resolver.resolve_link(
                schema.permalink,
                strict=True,
                load_relations=False,
            )

        if existing:
            logger.debug(f"Found existing entity: {existing.file_path}")
            return await self.update_entity(existing, self._coerce_schema_input(schema)), False
        else:
            # Create new entity
            return await self.create_entity(self._coerce_schema_input(schema)), True

    async def create_entity(self, schema: EntitySchema) -> EntityModel:
        """Create a new entity and write to filesystem."""
        return (await self.create_entity_with_content(schema)).entity

    async def create_entity_with_content(self, schema: EntitySchema) -> EntityWriteResult:
        """Create a new entity and return both the entity row and written markdown."""
        logger.debug(f"Creating entity: {schema.title}")
        # --- Prepare Accepted State ---
        # Derive the canonical markdown/entity fields before touching the filesystem.
        prepared = await self.prepare_create_entity_content(schema)
        self._sync_prepared_schema_state(schema, prepared)
        # --- Persist File, Then Indexable DB State ---
        # Local mode still writes the file immediately; the prepare object keeps semantics separate
        # from that persistence step.
        checksum = await self.file_service.write_file(prepared.file_path, prepared.markdown_content)
        entity = await self.upsert_entity_from_markdown(
            prepared.file_path,
            prepared.entity_markdown,
            is_new=True,
        )
        updated = await self.repository.update(entity.id, {"checksum": checksum})
        if not updated:  # pragma: no cover
            raise ValueError(f"Failed to update entity checksum after create: {entity.id}")
        persisted_content, search_content = await self._read_persisted_write_content(
            prepared.file_path
        )
        return EntityWriteResult(
            entity=updated,
            content=persisted_content,
            search_content=search_content,
        )

    async def update_entity(self, entity: EntityModel, schema: EntitySchema) -> EntityModel:
        """Update an entity's content and metadata."""
        return (
            await self.update_entity_with_content(entity, self._coerce_schema_input(schema))
        ).entity

    async def update_entity_with_content(
        self, entity: EntityModel, schema: EntitySchema
    ) -> EntityWriteResult:
        """Update an entity and return both the entity row and written markdown."""
        schema = self._coerce_schema_input(schema)
        logger.debug(
            f"Updating entity with permalink: {entity.permalink} content-type: {schema.content_type}"
        )

        # --- Read Current File State ---
        # Full replacements merge with existing frontmatter, so local mode still needs the current
        # file contents as input to the prepare step.
        existing_content = await self.file_service.read_file_content(entity.file_path)
        prepared = await self.prepare_update_entity_content(
            entity,
            schema,
            existing_content,
        )
        self._sync_prepared_schema_state(schema, prepared)
        previous_file_path = Path(entity.file_path)
        # Trigger: a full replacement also renames the note to a different canonical path.
        # Why: Path.replace() overwrites existing files, so the destination must be conflict-free
        #      before we write or we can clobber another note and only fail later at the DB layer.
        # Outcome: conflicting rename attempts fail before touching either file on disk.
        if (
            prepared.file_path.as_posix() != previous_file_path.as_posix()
            and await self.file_service.exists(prepared.file_path)
            and not self._paths_share_storage_target(previous_file_path, prepared.file_path)
        ):
            raise EntityAlreadyExistsError(
                f"file already exists at destination path: {prepared.file_path.as_posix()}"
            )
        # --- Persist Prepared State ---
        checksum = await self.file_service.write_file(
            prepared.file_path,
            prepared.markdown_content,
        )
        entity = await self.upsert_entity_from_markdown(
            prepared.file_path,
            prepared.entity_markdown,
            is_new=False,
            existing_entity=entity,
        )
        if prepared.file_path.as_posix() != previous_file_path.as_posix():
            # Trigger: a full replacement changed the canonical note path.
            # Why: the new file has already been written and the entity now points at it.
            # Outcome: remove the stale old file so local Basic Memory mirrors cloud's PGQ cleanup.
            if not self._paths_share_storage_target(previous_file_path, prepared.file_path):
                await self.file_service.delete_file(previous_file_path)
        entity = await self.repository.update(entity.id, {"checksum": checksum})
        if not entity:  # pragma: no cover
            raise ValueError(f"Failed to update entity checksum after update: {prepared.file_path}")
        persisted_content, search_content = await self._read_persisted_write_content(
            prepared.file_path
        )

        return EntityWriteResult(
            entity=entity,
            content=persisted_content,
            search_content=search_content,
        )

    async def delete_entity(self, permalink_or_id: str | int) -> bool:
        """Delete entity and its file."""
        logger.debug(f"Deleting entity: {permalink_or_id}")

        try:
            # Get entity first for file deletion
            if isinstance(permalink_or_id, str):
                entity = await self.get_by_permalink(permalink_or_id)
            else:
                entities = await self.get_entities_by_id([permalink_or_id])
                if len(entities) == 0:
                    # Entity already deleted (concurrent delete or race condition)
                    logger.info("Entity already deleted", entity_id=permalink_or_id)
                    return True
                if len(entities) != 1:  # pragma: no cover
                    logger.error(
                        "Entity lookup error", entity_id=permalink_or_id, found_count=len(entities)
                    )
                    raise ValueError(
                        f"Expected 1 entity with ID {permalink_or_id}, got {len(entities)}"
                    )
                entity = entities[0]

            # Delete from search index first (if search_service is available)
            if self.search_service:
                try:
                    await self.search_service.handle_delete(entity)
                except Exception:
                    # Search cleanup is best-effort during concurrent deletes.
                    # Relationships may have been cascade-deleted by a concurrent request.
                    logger.warning(
                        "Search cleanup failed for entity (likely concurrent delete)",
                        permalink_or_id=permalink_or_id,
                        exc_info=True,
                    )

            # Delete file
            await self.file_service.delete_entity_file(entity)

            # Delete from DB (this will cascade to observations/relations)
            # Trigger: repository.delete returns False when entity is already gone (NoResultFound)
            # Why: concurrent delete_directory requests can race to delete the same entity
            # Outcome: treat as success since the entity is deleted either way
            deleted = await self.repository.delete(entity.id)
            if not deleted:
                logger.info("Entity already removed from DB", entity_id=permalink_or_id)
            return True

        except EntityNotFoundError:
            logger.info(f"Entity not found: {permalink_or_id}")
            return True  # Already deleted

    async def get_by_permalink(self, permalink: str) -> EntityModel:
        """Get entity by type and name combination."""
        logger.debug(f"Getting entity by permalink: {permalink}")
        db_entity = await self.repository.get_by_permalink(permalink)
        if not db_entity:
            raise EntityNotFoundError(f"Entity not found: {permalink}")
        return db_entity

    async def get_entities_by_id(self, ids: List[int]) -> Sequence[EntityModel]:
        """Get specific entities and their relationships."""
        logger.debug(f"Getting entities: {ids}")
        return await self.repository.find_by_ids(ids)

    async def get_entities_by_permalinks(self, permalinks: List[str]) -> Sequence[EntityModel]:
        """Get specific nodes and their relationships."""
        logger.debug(f"Getting entities permalinks: {permalinks}")
        return await self.repository.find_by_permalinks(permalinks)

    async def delete_entity_by_file_path(self, file_path: Union[str, Path]) -> None:
        """Delete entity by file path."""
        await self.repository.delete_by_file_path(str(file_path))

    async def create_entity_from_markdown(
        self, file_path: Path, markdown: EntityMarkdown
    ) -> EntityModel:
        """Create entity and observations only.

        Creates the entity with null checksum to indicate sync not complete.
        Relations will be added in second pass.

        Uses UPSERT approach to handle permalink/file_path conflicts cleanly.
        """
        logger.debug(f"Creating entity: {markdown.frontmatter.title} file_path: {file_path}")
        model = entity_model_from_markdown(
            file_path, markdown, project_id=self.repository.project_id
        )

        # Mark as incomplete because we still need to add relations
        model.checksum = None

        # Set user tracking fields for cloud usage
        user_id = self.get_user_id()
        if user_id is not None:
            model.created_by = user_id
            model.last_updated_by = user_id

        # Use UPSERT to handle conflicts cleanly
        try:
            return await self.repository.upsert_entity(model)
        except Exception as e:
            logger.error(f"Failed to upsert entity for {file_path}: {e}")
            raise EntityCreationError(f"Failed to create entity: {str(e)}") from e

    async def update_entity_and_observations(
        self,
        file_path: Path,
        markdown: EntityMarkdown,
        *,
        existing_entity: EntityModel | None = None,
    ) -> EntityModel:
        """Update entity fields and observations.

        Updates everything except relations and sets null checksum
        to indicate sync not complete.
        """
        logger.debug(f"Updating entity and observations: {file_path}")

        db_entity = existing_entity
        if db_entity is not None:
            state = sa_inspect(db_entity)
            # Trigger: update flows can hand us an entity loaded in a different session.
            # Why: clearing and rebuilding observations touches relationship state that cannot
            #      lazy-load from a detached ORM instance.
            # Outcome: reload the canonical row before mutating observations.
            if state.detached or "observations" in state.unloaded:
                db_entity = await self.repository.get_by_id(db_entity.id)
        else:
            db_entity = await self.repository.get_by_file_path(file_path.as_posix())
        if db_entity is None:  # pragma: no cover
            raise EntityNotFoundError(f"Entity not found for file path: {file_path}")

        # Clear observations for entity
        await self.observation_repository.delete_by_fields(entity_id=db_entity.id)

        # add new observations
        observations = [
            Observation(
                project_id=self.observation_repository.project_id,
                entity_id=db_entity.id,
                content=obs.content,
                category=obs.category,
                context=obs.context,
                tags=obs.tags,
            )
            for obs in markdown.observations
        ]
        await self.observation_repository.add_all(observations)

        # update values from markdown
        db_entity = entity_model_from_markdown(file_path, markdown, db_entity)

        # checksum value is None == not finished with sync
        db_entity.checksum = None

        # Set last_updated_by for cloud usage (preserve existing created_by)
        user_id = self.get_user_id()
        if user_id is not None:
            db_entity.last_updated_by = user_id

        # update entity
        return await self.repository.update(
            db_entity.id,
            db_entity,
        )

    async def upsert_entity_from_markdown(
        self,
        file_path: Path,
        markdown: EntityMarkdown,
        *,
        is_new: bool,
        existing_entity: EntityModel | None = None,
    ) -> EntityModel:
        """Create/update entity and relations from parsed markdown."""
        if is_new:
            created = await self.create_entity_from_markdown(file_path, markdown)
        else:
            created = await self.update_entity_and_observations(
                file_path,
                markdown,
                existing_entity=existing_entity,
            )
        # Pass entity directly — avoids redundant get_by_file_path inside update_entity_relations
        return await self.update_entity_relations(created, markdown)

    async def update_entity_relations(
        self,
        entity: EntityModel,
        markdown: EntityMarkdown,
    ) -> EntityModel:
        """Update relations for entity.

        Accepts the entity object directly to avoid a redundant DB fetch.
        Only entity.id and entity.permalink are used from the passed-in object.
        """
        entity_id = entity.id
        logger.debug(f"Updating relations for entity: {entity.file_path}")

        # Clear existing relations first
        await self.relation_repository.delete_outgoing_relations_from_entity(entity_id)

        # Batch resolve all relation targets in parallel
        if markdown.relations:
            import asyncio

            # Create tasks for all relation lookups
            # Use strict=True to disable fuzzy search - only exact matches should create resolved relations
            # This ensures forward references (links to non-existent entities) remain unresolved (to_id=NULL)
            lookup_tasks = [
                self.link_resolver.resolve_link(
                    rel.target,
                    strict=True,
                    load_relations=False,
                )
                for rel in markdown.relations
            ]

            # Execute all lookups in parallel
            resolved_entities = await asyncio.gather(*lookup_tasks, return_exceptions=True)

            # Process results and create relation records
            relations_to_add = []
            for rel, resolved in zip(markdown.relations, resolved_entities):
                # Handle exceptions from gather and None results
                target_entity: Optional[Entity] = None
                if not isinstance(resolved, Exception):
                    # Type narrowing: resolved is Optional[Entity] here, not Exception
                    target_entity = resolved  # pyright: ignore [reportAssignmentType]

                # if the target is found, store the id
                target_id = target_entity.id if target_entity else None
                # if the target is found, store the title, otherwise add the target for a "forward link"
                target_name = target_entity.title if target_entity else rel.target

                # Create the relation
                relation = Relation(
                    project_id=self.relation_repository.project_id,
                    from_id=entity_id,
                    to_id=target_id,
                    to_name=target_name,
                    relation_type=rel.type,
                    context=rel.context,
                )
                relations_to_add.append(relation)

            # Batch insert all relations
            if relations_to_add:
                try:
                    await self.relation_repository.add_all(relations_to_add)
                except IntegrityError:
                    # Some relations might be duplicates - fall back to individual inserts
                    logger.debug("Batch relation insert failed, trying individual inserts")
                    for relation in relations_to_add:
                        try:
                            await self.relation_repository.add(relation)
                        except IntegrityError:
                            # Unique constraint violation - relation already exists
                            logger.debug(
                                f"Skipping duplicate relation {relation.relation_type} from {entity.permalink}"
                            )
                            continue

        # Reload entity with relations via PK lookup (faster than get_by_file_path string match)
        reloaded = await self.repository.find_by_ids([entity_id])
        return reloaded[0]

    async def edit_entity(
        self,
        identifier: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
    ) -> EntityModel:
        """Edit an existing entity's content using various operations.

        Args:
            identifier: Entity identifier (permalink, title, etc.)
            operation: The editing operation (append, prepend, find_replace, replace_section)
            content: The content to add or use for replacement
            section: For replace_section operation - the markdown header
            find_text: For find_replace operation - the text to find and replace
            expected_replacements: For find_replace operation - expected number of replacements (default: 1)

        Returns:
            The updated entity model

        Raises:
            EntityNotFoundError: If the entity cannot be found
            ValueError: If required parameters are missing for the operation or replacement count doesn't match expected
        """
        return (
            await self.edit_entity_with_content(
                identifier=identifier,
                operation=operation,
                content=content,
                section=section,
                find_text=find_text,
                expected_replacements=expected_replacements,
            )
        ).entity

    async def edit_entity_with_content(
        self,
        identifier: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
    ) -> EntityWriteResult:
        """Edit an entity and return both the entity row and written markdown."""
        logger.debug(f"Editing entity: {identifier}, operation: {operation}")

        entity = await self.link_resolver.resolve_link(
            identifier,
            strict=True,
            load_relations=False,
        )
        if not entity:
            raise EntityNotFoundError(f"Entity not found: {identifier}")

        file_path = Path(entity.file_path)
        current_content, _ = await self.file_service.read_file(file_path)
        # --- Prepare Against Explicit Base Content ---
        # The edit operation is the semantic step; file/DB writes below are just persistence of that
        # accepted result.
        prepared = await self.prepare_edit_entity_content(
            entity,
            current_content,
            operation=operation,
            content=content,
            section=section,
            find_text=find_text,
            expected_replacements=expected_replacements,
        )

        checksum = await self.file_service.write_file(
            file_path,
            prepared.markdown_content,
        )

        # --- Rebuild Structured Knowledge State ---
        # Non-fast edits remain fully synchronous locally: once the file write succeeds, we refresh
        # observations, relations, and checksum in the same request.
        entity = await self.upsert_entity_from_markdown(
            file_path,
            prepared.entity_markdown,
            is_new=False,
        )

        entity = await self.repository.update(entity.id, {"checksum": checksum})
        if not entity:  # pragma: no cover
            raise ValueError(f"Failed to update entity checksum after edit: {file_path}")
        persisted_content, search_content = await self._read_persisted_write_content(file_path)

        return EntityWriteResult(
            entity=entity,
            content=persisted_content,
            search_content=search_content,
        )

    def apply_edit_operation(
        self,
        current_content: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
    ) -> str:
        """Apply the specified edit operation to the current content."""

        if operation == "append":
            # Ensure proper spacing
            if current_content and not current_content.endswith("\n"):
                return current_content + "\n" + content
            return current_content + content  # pragma: no cover

        elif operation == "prepend":
            # Handle frontmatter-aware prepending
            return self._prepend_after_frontmatter(current_content, content)

        elif operation == "find_replace":
            if not find_text:
                raise ValueError("find_text is required for find_replace operation")
            if not find_text.strip():
                raise ValueError("find_text cannot be empty or whitespace only")

            # Count actual occurrences
            actual_count = current_content.count(find_text)

            # Validate count matches expected
            if actual_count != expected_replacements:
                if actual_count == 0:
                    raise ValueError(f"Text to replace not found: '{find_text}'")
                else:
                    raise ValueError(
                        f"Expected {expected_replacements} occurrences of '{find_text}', "
                        f"but found {actual_count}"
                    )

            return current_content.replace(find_text, content)

        elif operation == "replace_section":
            if not section:
                raise ValueError("section is required for replace_section operation")
            if not section.strip():
                raise ValueError("section cannot be empty or whitespace only")
            return self.replace_section_content(current_content, section, content)

        elif operation in ("insert_before_section", "insert_after_section"):
            if not section:
                raise ValueError("section is required for insert section operations")
            if not section.strip():
                raise ValueError("section cannot be empty or whitespace only")
            position = "before" if operation == "insert_before_section" else "after"
            return self.insert_relative_to_section(current_content, section, content, position)

        else:
            raise ValueError(f"Unsupported operation: {operation}")

    def replace_section_content(
        self, current_content: str, section_header: str, new_content: str
    ) -> str:
        """Replace content under a specific markdown section header.

        This method uses a simple, safe approach: when replacing a section, it only
        replaces the immediate content under that header until it encounters the next
        header of ANY level. This means:

        - Replacing "# Header" replaces content until "## Subsection" (preserves subsections)
        - Replacing "## Section" replaces content until "### Subsection" (preserves subsections)
        - More predictable and safer than trying to consume entire hierarchies

        Args:
            current_content: The current markdown content
            section_header: The section header to find and replace (e.g., "## Section Name")
            new_content: The new content to replace the section with (should not include the header itself)

        Returns:
            The updated content with the section replaced

        Raises:
            ValueError: If multiple sections with the same header are found
        """
        # Normalize the section header (ensure it starts with #)
        if not section_header.startswith("#"):
            section_header = "## " + section_header

        # Strip duplicate header from new_content if present (fix for issue #390)
        # LLMs sometimes include the section header in their content, which would create duplicates
        new_content_lines = new_content.lstrip().split("\n")
        if new_content_lines and new_content_lines[0].strip() == section_header.strip():
            # Remove the duplicate header line
            new_content = "\n".join(new_content_lines[1:]).lstrip()

        # First pass: count matching sections to check for duplicates
        lines = current_content.split("\n")
        matching_sections = []

        for i, line in enumerate(lines):
            if line.strip() == section_header.strip():
                matching_sections.append(i)

        # Handle multiple sections error
        if len(matching_sections) > 1:
            raise ValueError(
                f"Multiple sections found with header '{section_header}'. "
                f"Section replacement requires unique headers."
            )

        # If no section found, append it
        if len(matching_sections) == 0:
            logger.info(f"Section '{section_header}' not found, appending to end of document")
            separator = "\n\n" if current_content and not current_content.endswith("\n\n") else ""
            return current_content + separator + section_header + "\n" + new_content

        # Replace the single matching section
        result_lines = []
        section_line_idx = matching_sections[0]

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if this is our target section header
            if i == section_line_idx:
                # Add the section header and new content
                result_lines.append(line)
                result_lines.append(new_content)
                i += 1

                # Skip the original section content until next header or end
                while i < len(lines):
                    next_line = lines[i]
                    # Stop consuming when we hit any header (preserve subsections)
                    if next_line.startswith("#"):
                        # We found another header - continue processing from here
                        break
                    i += 1
                # Continue processing from the next header (don't increment i again)
                continue

            # Add all other lines (including subsequent sections)
            result_lines.append(line)
            i += 1

        return "\n".join(result_lines)

    def insert_relative_to_section(
        self,
        current_content: str,
        section_header: str,
        new_content: str,
        position: str,
    ) -> str:
        """Insert content before or after a section heading without consuming it.

        Unlike replace_section_content, this preserves the section heading and its
        existing content. The new content is inserted immediately before or after
        the heading line.

        Args:
            current_content: The current markdown content
            section_header: The section header to anchor on (e.g., "## Section Name")
            new_content: The content to insert
            position: "before" to insert above the heading, "after" to insert below it

        Returns:
            The updated content with new_content inserted relative to the heading

        Raises:
            ValueError: If the section header is not found or appears more than once
        """
        # Normalize the section header (ensure it starts with #)
        if not section_header.startswith("#"):
            section_header = "## " + section_header

        lines = current_content.split("\n")
        matching_indices = [
            i for i, line in enumerate(lines) if line.strip() == section_header.strip()
        ]

        if len(matching_indices) == 0:
            raise ValueError(
                f"Section '{section_header}' not found in document. "
                f"Use replace_section to create a new section."
            )
        if len(matching_indices) > 1:
            raise ValueError(
                f"Multiple sections found with header '{section_header}'. "
                f"Section insertion requires unique headers."
            )

        idx = matching_indices[0]

        if position == "before":
            # Insert new content before the section heading
            before = lines[:idx]
            after = lines[idx:]
            # Ensure blank line separation
            insert_lines = new_content.rstrip("\n").split("\n")
            if before and before[-1].strip() != "":
                insert_lines = [""] + insert_lines
            return "\n".join(before + insert_lines + [""] + after)
        else:
            # Insert new content after the section heading line
            before = lines[: idx + 1]
            after = lines[idx + 1 :]
            insert_lines = new_content.rstrip("\n").split("\n")
            # Ensure blank line separation so inserted text doesn't merge
            # with existing section content into a single paragraph
            if after and after[0].strip() != "":
                insert_lines = insert_lines + [""]
            return "\n".join(before + insert_lines + after)

    def _prepend_after_frontmatter(self, current_content: str, content: str) -> str:
        """Prepend content after frontmatter, preserving frontmatter structure."""

        # Trigger: the note starts with frontmatter delimiters.
        # Why: prepend must preserve the existing YAML block and insert content into the body,
        #      not silently rewrite malformed metadata into a corrupted accepted note state.
        # Outcome: valid frontmatter is preserved, and malformed frontmatter fails fast.
        if has_frontmatter(current_content):
            # Parse and separate frontmatter from body. Parse errors are intentional caller-visible
            # failures so prepare_edit_entity_content can reject unsafe accepted writes.
            frontmatter_data = parse_frontmatter(current_content)
            body_content = remove_frontmatter(current_content)

            # Prepend content to the body
            if content and not content.endswith("\n"):
                new_body = content + "\n" + body_content
            else:
                new_body = content + body_content

            # Reconstruct file with frontmatter + prepended body
            yaml_fm = yaml.dump(frontmatter_data, sort_keys=False, allow_unicode=True)
            return f"---\n{yaml_fm}---\n\n{new_body.strip()}"

        # No frontmatter means prepend is a plain text edit.
        if content and not content.endswith("\n"):
            return content + "\n" + current_content
        return content + current_content

    async def move_entity(
        self,
        identifier: str,
        destination_path: str,
        project_config: ProjectConfig,
        app_config: BasicMemoryConfig,
    ) -> EntityModel:
        """Move entity to new location with database consistency.

        Args:
            identifier: Entity identifier (title, permalink, or memory:// URL)
            destination_path: New path relative to project root
            project_config: Project configuration for file operations
            app_config: App configuration for permalink update settings

        Returns:
            Success message with move details

        Raises:
            EntityNotFoundError: If the entity cannot be found
            ValueError: If move operation fails due to validation or filesystem errors
        """
        logger.debug(f"Moving entity: {identifier} to {destination_path}")

        # 1. Resolve identifier to entity with strict mode for destructive operations
        entity = await self.link_resolver.resolve_link(identifier, strict=True)
        if not entity:
            raise EntityNotFoundError(f"Entity not found: {identifier}")

        current_path = entity.file_path
        old_permalink = entity.permalink

        # 2. Validate destination path format first
        if not destination_path or destination_path.startswith("/") or not destination_path.strip():
            raise ValueError(f"Invalid destination path: {destination_path}")

        # 3. Validate paths
        # NOTE: In tenantless/cloud mode, we cannot rely on local filesystem paths.
        # Use FileService for existence checks and moving.
        if not await self.file_service.exists(current_path):
            raise ValueError(f"Source file not found: {current_path}")

        if await self.file_service.exists(destination_path):
            raise ValueError(f"Destination already exists: {destination_path}")

        try:
            # 4. Ensure destination directory if needed (no-op for S3)
            await self.file_service.ensure_directory(Path(destination_path).parent)

            # 5. Move physical file via FileService (filesystem rename or cloud move)
            await self.file_service.move_file(current_path, destination_path)
            logger.info(f"Moved file: {current_path} -> {destination_path}")

            # 6. Prepare database updates
            updates = {"file_path": destination_path}

            # 7. Update permalink if configured or if entity has null permalink (unless disabled)
            if not app_config.disable_permalinks and (
                app_config.update_permalinks_on_move or old_permalink is None
            ):
                # Generate new permalink from destination path
                new_permalink = await self.resolve_permalink(destination_path)

                # Update frontmatter with new permalink
                await self.file_service.update_frontmatter(
                    destination_path, {"permalink": new_permalink}
                )

                updates["permalink"] = new_permalink
                if old_permalink is None:
                    logger.info(
                        f"Generated permalink for entity with null permalink: {new_permalink}"
                    )
                else:
                    logger.info(f"Updated permalink: {old_permalink} -> {new_permalink}")

            # 8. Recalculate checksum
            new_checksum = await self.file_service.compute_checksum(destination_path)
            updates["checksum"] = new_checksum

            # 9. Update database
            updated_entity = await self.repository.update(entity.id, updates)
            if not updated_entity:
                raise ValueError(f"Failed to update entity in database: {entity.id}")

            return updated_entity

        except Exception as e:
            # Rollback: try to restore original file location if move succeeded
            try:
                if await self.file_service.exists(
                    destination_path
                ) and not await self.file_service.exists(current_path):
                    await self.file_service.move_file(destination_path, current_path)
                    logger.info(f"Rolled back file move: {destination_path} -> {current_path}")
            except Exception as rollback_error:  # pragma: no cover
                logger.error(f"Failed to rollback file move: {rollback_error}")

            # Re-raise the original error with context
            raise ValueError(f"Move failed: {str(e)}") from e

    async def move_directory(
        self,
        source_directory: str,
        destination_directory: str,
        project_config: ProjectConfig,
        app_config: BasicMemoryConfig,
    ) -> DirectoryMoveResult:
        """Move all entities in a directory to a new location.

        This operation moves all files within a source directory to a destination
        directory, updating database records and search indexes. The operation
        tracks successes and failures individually to provide detailed feedback.

        Args:
            source_directory: Source directory path relative to project root
            destination_directory: Destination directory path relative to project root
            project_config: Project configuration for file operations
            app_config: App configuration for permalink update settings

        Returns:
            DirectoryMoveResult with counts and details of moved files

        Raises:
            ValueError: If source directory is empty or destination conflicts exist
        """

        logger.info(f"Moving directory: {source_directory} -> {destination_directory}")

        # Normalize directory paths (remove trailing slashes)
        source_directory = source_directory.strip("/")
        destination_directory = destination_directory.strip("/")

        # Find all entities in the source directory
        entities = await self.repository.find_by_directory_prefix(source_directory)

        if not entities:
            logger.warning(f"No entities found in directory: {source_directory}")
            return DirectoryMoveResult(
                total_files=0,
                successful_moves=0,
                failed_moves=0,
                moved_files=[],
                errors=[],
            )

        # Track results
        moved_files: list[str] = []
        errors: list[DirectoryMoveError] = []
        successful_moves = 0
        failed_moves = 0

        # Process each entity
        for entity in entities:
            try:
                # Calculate new path by replacing source prefix with destination
                old_path = entity.file_path
                # Replace only the first occurrence of the source directory prefix
                if old_path.startswith(f"{source_directory}/"):
                    new_path = old_path.replace(
                        f"{source_directory}/", f"{destination_directory}/", 1
                    )
                else:  # pragma: no cover
                    # Entity is directly in the source directory (shouldn't happen with prefix match)
                    new_path = f"{destination_directory}/{old_path}"

                # Move the individual entity
                await self.move_entity(
                    identifier=entity.file_path,
                    destination_path=new_path,
                    project_config=project_config,
                    app_config=app_config,
                )

                moved_files.append(new_path)
                successful_moves += 1
                logger.debug(f"Moved entity: {old_path} -> {new_path}")

            except Exception as e:  # pragma: no cover
                failed_moves += 1
                errors.append(DirectoryMoveError(path=entity.file_path, error=str(e)))
                logger.error(f"Failed to move entity {entity.file_path}: {e}")

        logger.info(
            f"Directory move complete: {successful_moves} succeeded, {failed_moves} failed "
            f"(source={source_directory}, dest={destination_directory})"
        )

        return DirectoryMoveResult(
            total_files=len(entities),
            successful_moves=successful_moves,
            failed_moves=failed_moves,
            moved_files=moved_files,
            errors=errors,
        )

    async def delete_directory(
        self,
        directory: str,
    ) -> DirectoryDeleteResult:
        """Delete all entities in a directory.

        This operation deletes all files within a directory, updating database
        records and search indexes. The operation tracks successes and failures
        individually to provide detailed feedback.

        Args:
            directory: Directory path relative to project root

        Returns:
            DirectoryDeleteResult with counts and details of deleted files
        """
        logger.info(f"Deleting directory: {directory}")

        # Normalize directory path (remove trailing slashes)
        directory = directory.strip("/")

        # Find all entities in the directory
        entities = await self.repository.find_by_directory_prefix(directory)

        if not entities:
            logger.warning(f"No entities found in directory: {directory}")
            return DirectoryDeleteResult(
                total_files=0,
                successful_deletes=0,
                failed_deletes=0,
                deleted_files=[],
                errors=[],
            )

        # Track results
        deleted_files: list[str] = []
        errors: list[DirectoryDeleteError] = []
        successful_deletes = 0
        failed_deletes = 0

        # Process each entity
        for entity in entities:
            try:
                file_path = entity.file_path

                # Delete the entity (this handles file deletion and database cleanup)
                deleted = await self.delete_entity(entity.id)

                if deleted:
                    deleted_files.append(file_path)
                    successful_deletes += 1
                    logger.debug(f"Deleted entity: {file_path}")
                else:  # pragma: no cover
                    failed_deletes += 1
                    errors.append(
                        DirectoryDeleteError(path=file_path, error="Delete returned False")
                    )
                    logger.warning(f"Delete returned False for entity: {file_path}")

            except Exception as e:  # pragma: no cover
                failed_deletes += 1
                errors.append(DirectoryDeleteError(path=entity.file_path, error=str(e)))
                logger.error(f"Failed to delete entity {entity.file_path}: {e}")

        logger.info(
            f"Directory delete complete: {successful_deletes} succeeded, {failed_deletes} failed "
            f"(directory={directory})"
        )

        return DirectoryDeleteResult(
            total_files=len(entities),
            successful_deletes=successful_deletes,
            failed_deletes=failed_deletes,
            deleted_files=deleted_files,
            errors=errors,
        )
