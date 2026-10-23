"""bm_edit: change part of a note (SPEC-35.2, #1831).

Replaces edit_note. The note must already exist and resolve exactly: bm_edit never
creates a note (bm_write does) and never edits a near match. `section` uses the
selector bm_read takes, so a section read and a section edit name it the same way.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, BeforeValidator, Field

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import open_project_target, resolve_note
from basic_memory.mcp.note_writes import format_note_change, note_change_result
from basic_memory.mcp.refusals import api_refusal, refusal
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
        identifier, project, context, output_format=output_format
    ) as target:
        note = await resolve_note(
            target,
            identifier,
            output_format=output_format,
            try_instead=f"bm_write(identifier='{identifier}', content='...') to create it",
        )
        if not runtime_file_path_is_markdown_note(note.file_path):
            raise refusal(
                "UNSUPPORTED",
                f"'{note.file_path}' is not a Markdown note; bm_edit changes notes only.",
                output_format=output_format,
            )
        try:
            edited = await KnowledgeClient(target.client, target.project.external_id).patch_entity(
                note.external_id, edit, base_checksum=expected_checksum
            )
        except ToolError as error:
            raise _edit_refusal(error, note.file_path, output_format) from error

    result = note_change_result(edited, action="edited", paths=target.paths)
    result["command"] = command
    if output_format == "json":
        return result
    return "\n".join(
        format_note_change(result, headline=f"Edited {result['file_path']} ({command})")
    )


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


def _edit_refusal(error: ToolError, file_path: str, output_format: OutputFormat) -> ToolError:
    """Explain a failed PATCH: a refusal changed nothing; no answer is an unknown outcome."""
    refused = api_refusal(error)
    if refused is None:
        # Trigger: the request left and no 4xx came back (no response, or a 5xx).
        # Why: the server may have applied the edit; retrying an append would apply
        #      it twice.
        # Outcome: an error that says to read the note before retrying.
        return refusal(
            "OUTCOME_UNKNOWN",
            f"The edit to '{file_path}' may or may not have been applied: {error}",
            try_instead=f"bm_read(identifier='{file_path}') before retrying",
            output_format=output_format,
        )
    if refused.revision_conflict and refused.current_checksum is None:
        return refusal(
            "REVISION_CONFLICT",
            f"'{file_path}' was deleted since you read it. Nothing was changed.",
            try_instead="bm_search to find where its content went",
            output_format=output_format,
        )
    if refused.revision_conflict:
        return refusal(
            "REVISION_CONFLICT",
            f"'{file_path}' changed since you read it (current checksum "
            f"{refused.current_checksum}). Nothing was changed.",
            try_instead=(
                f"bm_read(identifier='{file_path}'), then retry with "
                f"expected_checksum='{refused.current_checksum}'"
            ),
            output_format=output_format,
        )
    return refusal(
        refused.code,
        refused.with_outcome("Nothing was changed."),
        try_instead=f"bm_read(identifier='{file_path}') to see the note as it is",
        output_format=output_format,
    )
