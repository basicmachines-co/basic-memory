"""bm_write: create or replace a note.

A note is addressed one of two ways:

- by path (`identifier="notes/plan.md"`): the file lands exactly there, and its title
  comes from the content's frontmatter, then `title`, then the file name;
- by `title` + `directory`: the file name is derived from the title.

bm_write is the only tool that creates notes. The API resolves the folder against the
folders that already exist, so `plans/` beside an existing `Plans/` writes into
`Plans/`; the result reports the path actually written.
"""

import dataclasses
from pathlib import PurePosixPath
from typing import Annotated, Any, assert_never

from fastmcp import Context
from fastmcp.exceptions import ToolError
from loguru import logger
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.config import ConfigManager
from basic_memory.file_utils import ParseError, has_frontmatter, parse_frontmatter
from basic_memory.mcp.async_client import local_project_path
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.note_targets import ProjectTarget, open_project_target
from basic_memory.mcp.note_writes import NoteChange
from basic_memory.mcp.refusals import api_refusal, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.similar_notes import (
    SimilarNote,
    find_similar_notes,
    format_similar_notes_section,
    probe_declined_by_server,
    similar_notes_min_similarity,
)
from basic_memory.mcp.tool_params import OutputFormat, ProjectParam
from basic_memory.schemas.base import Entity
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.v2.entity import EntityResponseV2
from basic_memory.schemas.v2.note_write import (
    NoteAlreadyExists,
    NoteCreated,
    NoteLocked,
    NoteRevisionConflict,
    NoteTargetMoved,
    NoteUpdated,
)
from basic_memory.utils import coerce_dict, parse_tags, validate_project_path

MARKDOWN_SUFFIX = ".md"


@dataclasses.dataclass(frozen=True, slots=True)
class NotePlacement:
    """Where a write puts the note: a directory, and a file name or None to derive one."""

    directory: str
    file_name: str | None
    title: str


@mcp.tool(
    name="bm_write",
    title="Write note",
    description=(
        "Create or replace a Basic Memory note. Give a file path as `identifier` "
        "('notes/plan.md'), or `title` plus `directory`. With a path the file lands exactly "
        "there and the title comes from the content's frontmatter, then `title`, then the "
        "file name. An existing note is an error unless `overwrite=true`; pass "
        "`expected_checksum` (from bm_read) to replace only the revision you read. To change "
        "part of a note use bm_edit. Content may hold frontmatter, observations "
        "('- [category] fact #tag') and relations ('- relation_type [[Other Note]]')."
    ),
    tags={"notes"},
    annotations={
        "title": "Write note",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_write(
    content: Annotated[
        str,
        Field(description="The note's Markdown. Saved as given; it may start with frontmatter."),
    ],
    identifier: Annotated[
        str | None,
        Field(
            min_length=1,
            # `file_path` is the spelling of Claude Code's built-in Write tool (C12).
            validation_alias=AliasChoices("identifier", "file_path"),
            description=(
                "File path for the note, e.g. 'notes/plan.md'; '.md' is added when missing. "
                "'<project>/path' writes into another project. Use instead of "
                "title + directory."
            ),
        ),
    ] = None,
    title: Annotated[
        str | None,
        Field(
            min_length=1,
            description=(
                "Note title. With `directory`, it also names the file. With `identifier`, it "
                "is used when the content has no frontmatter title."
            ),
        ),
    ] = None,
    directory: Annotated[
        str | None,
        Field(
            validation_alias=AliasChoices("directory", "folder", "dir"),
            description="Folder for a note addressed by title, e.g. 'meetings'; '/' is the root.",
        ),
    ] = None,
    note_type: Annotated[
        str, Field(description="Frontmatter `type:`. A `type:` in the content wins.")
    ] = "note",
    tags: Annotated[
        list[str] | str | None, Field(description="Tags, as a list or 'a,b,c'.")
    ] = None,
    metadata: Annotated[
        dict[str, Any] | None,
        # Some MCP clients serialize objects as JSON strings.
        BeforeValidator(coerce_dict),
        Field(description="Extra frontmatter fields, merged into the note's frontmatter."),
    ] = None,
    overwrite: Annotated[
        bool | None,
        Field(
            description=(
                "Replace a note that already exists. Default: the "
                "write_note_overwrite_default setting (false unless changed)."
            )
        ),
    ] = None,
    expected_checksum: Annotated[
        str | None,
        Field(
            min_length=1,
            description=(
                "Replace the note only while it is still this revision (the checksum from "
                "bm_read). Implies overwrite=true."
            ),
        ),
    ] = None,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Create or replace one note."""
    # --- Argument combinations ---
    if identifier is not None and directory is not None:
        raise refusal(
            "INVALID_ARGUMENTS",
            "Give either `identifier` (a file path) or `title` + `directory`, not both.",
            try_instead=f"bm_write(identifier='{directory.strip('/')}/<file>.md', ...)",
            output_format=output_format,
        )
    if identifier is None and (title is None or directory is None):
        raise refusal(
            "INVALID_ARGUMENTS",
            "Say where the note goes: `identifier` (a file path) or `title` + `directory`.",
            try_instead="bm_write(identifier='notes/plan.md', content='...')",
            output_format=output_format,
        )
    # Trigger: the caller pinned a revision but did not say overwrite.
    # Why: a checksum only means something for a replacement, so asking for one is
    #      asking to replace; an explicit overwrite=false contradicts it.
    # Outcome: expected_checksum turns overwrite on, or refuses the contradiction.
    if expected_checksum is not None and overwrite is False:
        raise refusal(
            "INVALID_ARGUMENTS",
            "expected_checksum replaces an existing revision, so it needs overwrite=true.",
            output_format=output_format,
        )
    effective_overwrite = (
        True
        if expected_checksum is not None
        else overwrite
        if overwrite is not None
        else ConfigManager().config.write_note_overwrite_default
    )

    # Routing uses the identifier only. A title-addressed note goes to `project`; its
    # directory is a folder name and never selects a project.
    async with open_project_target(
        identifier or "/", project, context, output_format=output_format
    ) as target:
        placement = _place_note(
            target,
            identifier=identifier,
            title=title,
            directory=directory,
            content=content,
            output_format=output_format,
        )
        entity_metadata = dict(metadata or {})
        tag_list = parse_tags(tags)
        if tag_list:
            entity_metadata["tags"] = tag_list
        note = Entity(
            title=placement.title,
            directory=placement.directory,
            file_name=placement.file_name,
            note_type=note_type,
            content_type="text/markdown",
            content=content,
            entity_metadata=entity_metadata or None,
        )
        knowledge_client = KnowledgeClient(target.client, target.project.external_id)
        try:
            outcome = await knowledge_client.write_note(
                note, overwrite=effective_overwrite, expected_checksum=expected_checksum
            )
        except ToolError as error:
            raise _write_failure(error, target.paths.path(note.file_path), output_format) from error

        # --- Outcomes ---
        match outcome:
            case NoteCreated(entity=written):
                action = "created"
            case NoteUpdated(entity=written):
                action = "updated"
            case NoteAlreadyExists(file_path=file_path, permalink=permalink):
                shown = target.paths.path(file_path)
                existing = permalink or shown
                raise refusal(
                    "ALREADY_EXISTS",
                    f"A note already exists at '{shown}'. Nothing was written.",
                    try_instead=(
                        f"bm_edit(identifier='{existing}', command='append', content='...') to "
                        "add to it, or bm_write(..., overwrite=true) to replace it"
                    ),
                    output_format=output_format,
                )
            case NoteTargetMoved(file_path=file_path, external_id=external_id):
                shown = target.paths.path(file_path)
                raise refusal(
                    "ALREADY_EXISTS",
                    f"The note this path names has moved to '{shown}'. Nothing was written.",
                    try_instead=(
                        f"bm_read(identifier='{external_id}') to work on that note, or "
                        "overwrite=false to create a separate note at this path"
                    ),
                    output_format=output_format,
                )
            case NoteLocked(message=message):
                raise refusal("LOCKED", message, output_format=output_format)
            case NoteRevisionConflict(file_path=file_path, db_checksum=None):
                shown = target.paths.path(file_path)
                raise refusal(
                    "REVISION_CONFLICT",
                    f"No note exists at '{shown}' any more, so nothing was replaced.",
                    try_instead="bm_write without expected_checksum to create it",
                    output_format=output_format,
                )
            case NoteRevisionConflict(file_path=file_path, db_checksum=current_checksum):
                shown = target.paths.path(file_path)
                raise refusal(
                    "REVISION_CONFLICT",
                    f"'{shown}' changed since you read it (current checksum "
                    f"{current_checksum}). Nothing was replaced.",
                    try_instead=(
                        f"bm_read(identifier='{shown}'), then retry with "
                        f"expected_checksum='{current_checksum}'"
                    ),
                    output_format=output_format,
                )
            case _:  # pragma: no cover
                assert_never(outcome)

        similar_notes = (
            await _similar_notes(target, knowledge_client, written, content)
            if action == "created"
            else []
        )

    # Trigger: the API wrote into an existing folder's spelling.
    # Why: the caller should learn the real path rather than keep using its guess.
    # Outcome: the result names the path that was asked for next to the one written.
    change = NoteChange.of(
        written,
        action=action,
        paths=target.paths,
        requested_file_path=_requested_path(placement),
    )
    if output_format == "json":
        return {
            **change.as_json(),
            "similar_notes": [dataclasses.asdict(similar) for similar in similar_notes],
        }
    return _format_text(change, similar_notes)


def _write_failure(error: ToolError, file_path: str, output_format: OutputFormat) -> ToolError:
    """Explain a write the API did not answer with a typed outcome."""
    refused = api_refusal(error)
    if refused is None:
        # The request left and no 4xx came back, so the note may have been written.
        return refusal(
            "OUTCOME_UNKNOWN",
            f"The write to '{file_path}' may or may not have landed: {error}",
            try_instead=f"bm_read(identifier='{file_path}') before retrying",
            output_format=output_format,
        )
    return refusal(
        refused.code, refused.with_outcome("Nothing was written."), output_format=output_format
    )


def _place_note(
    target: ProjectTarget,
    *,
    identifier: str | None,
    title: str | None,
    directory: str | None,
    content: str,
    output_format: OutputFormat,
) -> NotePlacement:
    """Turn the caller's address into a directory, file name and title."""
    # --- Title + directory ---
    if identifier is None:
        assert title is not None and directory is not None  # checked by the caller
        folder = directory.strip("/")
        if folder and not validate_project_path(
            folder, local_project_path(target.client, target.project)
        ):
            raise refusal(
                "OUTSIDE_PROJECT",
                f"Directory '{directory}' is outside the project.",
                try_instead="a folder relative to the project root, such as 'notes'",
                output_format=output_format,
            )
        return NotePlacement(directory=folder, file_name=None, title=title)

    # --- File path ---
    relative = memory_url_path(target.route.path).lstrip("/")
    path = PurePosixPath(relative)
    # A trailing '/' (or a bare project name) names a folder, never a file.
    if not path.name or relative.endswith("/"):
        raise refusal(
            "INVALID_ARGUMENTS",
            f"'{identifier}' names a folder or project, not a note file.",
            try_instead=f"bm_write(identifier='{identifier.rstrip('/')}/<file>.md', ...)",
            output_format=output_format,
        )
    # Trigger: the name has some other extension ('data.json', 'v1.2').
    # Why: bm_write writes Markdown notes; silently making 'data.json.md' would hide
    #      the mistake, and writing JSON as a note would index it as Markdown.
    # Outcome: a name without an extension gets '.md'; any other extension is refused.
    if path.suffix.lower() != MARKDOWN_SUFFIX:
        if path.suffix:
            raise refusal(
                "UNSUPPORTED",
                f"'{path.name}' is not a Markdown file name; bm_write writes '.md' notes.",
                try_instead=f"bm_write(identifier='{path.with_name(path.name + MARKDOWN_SUFFIX)}')",
                output_format=output_format,
            )
        path = path.with_name(path.name + MARKDOWN_SUFFIX)
    return NotePlacement(
        directory="" if str(path.parent) == "." else str(path.parent),
        file_name=path.name,
        title=_frontmatter_title(content) or title or path.stem,
    )


def _frontmatter_title(content: str) -> str | None:
    """The `title:` in the content's frontmatter, if it has a usable one."""
    if not has_frontmatter(content):
        return None
    try:
        frontmatter = parse_frontmatter(content)
    except ParseError:
        # Malformed frontmatter is reported by the write itself, with the API's message.
        return None
    value = frontmatter.get("title")
    if value is None:
        return None
    return str(value).strip() or None


def _requested_path(placement: NotePlacement) -> str | None:
    """The path the caller asked for, when it named the file; None for title writes."""
    if placement.file_name is None:
        return None
    if not placement.directory:
        return placement.file_name
    return f"{placement.directory}/{placement.file_name}"


async def _similar_notes(
    target: ProjectTarget,
    knowledge_client: KnowledgeClient,
    written: EntityResponseV2,
    content: str,
) -> list[SimilarNote]:
    """List existing notes near a newly created one; advisory only.

    The probe is always attempted: whether semantic search is on is the routed
    server's setting, and its refusal (a ToolError) only means there is no advisory.
    """
    try:
        return await find_similar_notes(
            SearchClient(target.client, target.project.external_id),
            knowledge_client,
            title=written.title,
            content=content,
            exclude_file_path=written.file_path,
            exclude_permalink=written.permalink,
            min_similarity=similar_notes_min_similarity(ConfigManager().config),
        )
    except ToolError as probe_error:
        # The note is already written, so a failed probe must not fail the call.
        probe_log = logger.debug if probe_declined_by_server(probe_error) else logger.warning
        probe_log(f"bm_write similar-note probe failed for {written.file_path}: {probe_error}")
        return []


def _format_text(change: NoteChange, similar_notes: list[SimilarNote]) -> str:
    """The text result: what was written, where, and the checksum to pass back."""
    lines = change.text_lines()
    if similar_notes:
        new_note = change.permalink or change.file_path
        lines.append(
            format_similar_notes_section(
                similar_notes,
                same_topic=lambda closest: (
                    f"`bm_edit(identifier='{closest}', command='append', content='...')`, "
                    f"then `bm_delete(identifier='{new_note}')`"
                ),
            )
        )
    return "\n".join(lines)
