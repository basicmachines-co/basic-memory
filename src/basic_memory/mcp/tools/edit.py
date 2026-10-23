"""bm_edit: change part of a note.

The note must already exist and resolve exactly: bm_edit never creates a note
(bm_write does) and never edits a near match. `section` uses the selector bm_read
takes, so a section read and a section edit name it the same way.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import open_project_target, resolve_note
from basic_memory.mcp.note_writes import NoteChange
from basic_memory.mcp.refusals import note_change_failure, refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import NoteIdentifier, OutputFormat, ProjectParam
from basic_memory.runtime.storage import runtime_file_path_is_markdown_note
from basic_memory.utils import coerce_dict

type EditCommand = Literal[
    "append",
    "prepend",
    "find_replace",
    "replace_section",
    "insert_before_section",
    "insert_after_section",
    "set_metadata",
]

SECTION_COMMANDS: frozenset[EditCommand] = frozenset(
    {"replace_section", "insert_before_section", "insert_after_section"}
)


@mcp.tool(
    name="bm_edit",
    title="Edit note",
    description=(
        "Change part of an existing Basic Memory note. `command`: append or prepend "
        "`content`; find_replace `find_text` with `content` (exact text, which must occur "
        "`expected_replacements` times, default 1); replace_section, insert_before_section "
        "or insert_after_section for a `section` heading ('Decisions', 'Auth/Decisions', "
        "'Decisions[1]', as bm_read takes it); set_metadata to merge `metadata` into the "
        "frontmatter without touching the body. `metadata` also merges with any other "
        "command. Pass `expected_checksum` (from bm_read) to edit only the revision you "
        "read. A missing note is an error; create notes with bm_write."
    ),
    tags={"notes"},
    annotations={
        "title": "Edit note",
        "readOnlyHint": False,
        # find_replace and replace_section remove text.
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def bm_edit(
    identifier: NoteIdentifier,
    command: Annotated[
        EditCommand,
        Field(validation_alias=AliasChoices("command", "operation"), description="The edit."),
    ],
    content: Annotated[
        str | None,
        Field(
            # Claude Code's Edit tool says new_string (C12); diff-style APIs say the rest.
            validation_alias=AliasChoices(
                "content", "new_string", "new_content", "replacement", "replace_with"
            ),
            description="The text to add or the replacement. Not used by set_metadata.",
        ),
    ] = None,
    section: Annotated[
        str | None,
        Field(
            validation_alias=AliasChoices("section", "heading", "section_heading"),
            description=(
                'The heading for section commands: "Decisions", "Auth/Decisions" for one '
                'under "Auth", or "Decisions[1]" for the second of two.'
            ),
        ),
    ] = None,
    find_text: Annotated[
        str | None,
        Field(
            min_length=1,
            validation_alias=AliasChoices(
                "find_text", "old_string", "find", "old_text", "old_content", "search"
            ),
            description="For find_replace: the exact, case-sensitive text to replace.",
        ),
    ] = None,
    expected_replacements: Annotated[
        int,
        Field(ge=1, description="For find_replace: how many times find_text must occur."),
    ] = 1,
    replace_subsections: Annotated[
        bool,
        Field(
            description=(
                "For replace_section: true replaces the section's subsections too; false "
                "stops at the first subsection heading."
            )
        ),
    ] = True,
    metadata: Annotated[
        dict[str, Any] | None,
        # Some MCP clients serialize objects as JSON strings.
        BeforeValidator(coerce_dict),
        Field(description="Frontmatter fields to set; other fields are kept."),
    ] = None,
    expected_checksum: Annotated[
        str | None,
        Field(
            min_length=1,
            description="Edit only while the note is still this revision (from bm_read).",
        ),
    ] = None,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Edit one existing note."""
    _check_arguments(
        command,
        content=content,
        section=section,
        find_text=find_text,
        metadata=metadata,
        output_format=output_format,
    )
    edit: dict[str, Any] = {
        "operation": command,
        "content": content or "",
        "expected_replacements": expected_replacements,
        "replace_subsections": replace_subsections,
    }
    if section is not None:
        edit["section"] = section
    if find_text is not None:
        edit["find_text"] = find_text
    if metadata:
        edit["metadata"] = metadata

    async with open_project_target(
        identifier, project, context, kind="identifier", output_format=output_format
    ) as target:
        note = await resolve_note(
            target,
            identifier,
            output_format=output_format,
            try_instead=f"bm_write(identifier='{identifier}', content='...') to create it",
            expected_checksum=expected_checksum,
            unchanged="Nothing was changed.",
        )
        if not runtime_file_path_is_markdown_note(note.file_path):
            raise refusal(
                "UNSUPPORTED",
                f"'{target.paths.path(note.file_path)}' is not a Markdown note; bm_edit changes "
                "notes only.",
                output_format=output_format,
            )
        try:
            edited = await KnowledgeClient(target.client, target.project.external_id).patch_entity(
                note.external_id, edit, base_checksum=expected_checksum
            )
        except ToolError as error:
            raise note_change_failure(
                error,
                action="edit",
                address=target.paths.path(note.file_path),
                unchanged="Nothing was changed.",
                output_format=output_format,
            ) from error

    change = NoteChange.of(edited, action="edited", paths=target.paths)
    if output_format == "json":
        return {**change.as_json(), "command": command}
    return "\n".join(change.text_lines(detail=command))


def _check_arguments(
    command: EditCommand,
    *,
    content: str | None,
    section: str | None,
    find_text: str | None,
    metadata: dict[str, Any] | None,
    output_format: OutputFormat,
) -> None:
    """Refuse argument combinations the command cannot use, before any request."""

    def invalid(message: str) -> ToolError:
        return refusal("INVALID_ARGUMENTS", message, output_format=output_format)

    if command == "set_metadata":
        if not metadata:
            raise invalid("set_metadata needs `metadata`, the frontmatter fields to set.")
        if content:
            raise invalid("set_metadata changes frontmatter only; leave `content` out.")
    elif content is None:
        raise invalid(f"{command} needs `content`.")
    if command == "find_replace" and find_text is None:
        raise invalid("find_replace needs `find_text`, the exact text to replace.")
    if command != "find_replace" and find_text is not None:
        raise invalid(f"`find_text` is only for find_replace, not {command}.")
    if command in SECTION_COMMANDS and section is None:
        raise invalid(f"{command} needs `section`, the heading to edit.")
    if command not in SECTION_COMMANDS and section is not None:
        raise invalid(f"`section` is only for section commands, not {command}.")
    # YAML null would be written, then dropped by indexing; deletion is its own feature.
    null_keys = sorted(key for key, value in (metadata or {}).items() if value is None)
    if null_keys:
        raise invalid(
            f"metadata values cannot be null (removing keys is not supported): {null_keys}"
        )
    # The frontmatter merge keeps a note's identity out of `metadata`, so these keys
    # would be acknowledged and never applied.
    identity_keys = sorted({"title", "permalink"} & set(metadata or {}))
    if identity_keys:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"metadata cannot change {', '.join(identity_keys)}; those are the note's identity.",
            try_instead=(
                "bm_move to rename the file, or edit the title in the note text "
                "(command='find_replace')"
            ),
            output_format=output_format,
        )
