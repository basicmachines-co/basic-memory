"""bm_read: read a note or file (SPEC-35.2, #1830).

Replaces read_note, cat, view_note and read_content. A Markdown note comes back as
its text, whole or sliced by section, line range or token budget. Any other file
comes back the way an MCP client can show it: text as text, images as image
content, other binaries as an embedded document.
"""

from pathlib import PurePosixPath
from typing import Annotated, Any

from fastmcp import Context
from fastmcp.utilities.types import File, Image
from mcp.types import EmbeddedResource, ImageContent
from pydantic import Field

from basic_memory.markdown.line_scanning import format_line_read
from basic_memory.mcp.clients import KnowledgeClient, ResourceClient
from basic_memory.mcp.file_content import (
    DocumentFile,
    DocumentTooLarge,
    FileContent,
    ImageFile,
    TextFile,
    UnsupportedImage,
    classify_file_response,
)
from basic_memory.mcp.note_reads import format_note_metadata, read_note_json_by_external_id
from basic_memory.mcp.note_targets import open_project_target, resolve_note
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import NoteIdentifier, OutputFormat, ProjectParam
from basic_memory.runtime.storage import runtime_file_path_is_markdown_note

LineNumber = Annotated[int | None, Field(ge=1)]


@mcp.tool(
    name="bm_read",
    title="Read note",
    description=(
        "Read a Basic Memory note or file by path, permalink, memory:// URL or exact title. "
        "Slice a long note with `section` (a heading), `start_line`/`end_line` or "
        "`max_tokens`; line numbers count the whole file, frontmatter included. "
        "`include_content=false` returns only the metadata and the checksum that "
        "bm_write and bm_edit take as expected_checksum. Images come back as images and "
        "other binary files as documents. A missing note is an error: find notes with "
        "bm_search."
    ),
    tags={"notes"},
    annotations={
        "title": "Read note",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_read(
    identifier: NoteIdentifier,
    section: Annotated[
        str | None,
        Field(
            description=(
                'A heading to read: "Decisions", "Auth/Decisions" for a heading under '
                '"Auth", or "Decisions[1]" for the second "Decisions" heading.'
            )
        ),
    ] = None,
    start_line: LineNumber = None,
    end_line: LineNumber = None,
    max_tokens: Annotated[
        int | None,
        Field(
            ge=1,
            description=(
                "Approximate token budget. Longer content is cut at a section or paragraph "
                "boundary, and the result says which line to continue from."
            ),
        ),
    ] = None,
    include_frontmatter: Annotated[
        bool, Field(description="Include the YAML frontmatter in a whole-note read.")
    ] = True,
    include_content: Annotated[
        bool,
        Field(description="False returns only title, paths, checksum and updated_at."),
    ] = True,
    output_format: OutputFormat = "text",
    project: ProjectParam = None,
    context: Context | None = None,
) -> str | dict[str, Any] | ImageContent | EmbeddedResource:
    """Read one note or file."""
    # --- Argument combinations ---
    line_range = start_line is not None or end_line is not None
    sliced = section is not None or line_range or max_tokens is not None
    if section is not None and line_range:
        raise refusal(
            "INVALID_ARGUMENTS",
            "section cannot be combined with start_line/end_line.",
            try_instead="read the section, then use the start_line/end_line it returns",
            output_format=output_format,
        )
    if start_line is not None and end_line is not None and end_line < start_line:
        raise refusal(
            "INVALID_ARGUMENTS",
            f"end_line ({end_line}) is before start_line ({start_line}).",
            output_format=output_format,
        )
    if not include_content and sliced:
        raise refusal(
            "INVALID_ARGUMENTS",
            "include_content=false cannot be combined with section, lines or max_tokens.",
            output_format=output_format,
        )

    async with open_project_target(
        identifier, project, context, output_format=output_format
    ) as target:
        note = await resolve_note(target, identifier, output_format=output_format)
        knowledge_client = KnowledgeClient(target.client, target.project.external_id)

        # --- Metadata only ---
        if not include_content:
            entity = await knowledge_client.get_entity(note.external_id)
            metadata = {
                "title": entity.title,
                "permalink": entity.permalink,
                "file_path": entity.file_path,
                "checksum": entity.db_checksum,
                "updated_at": entity.updated_at.isoformat(),
            }
            return metadata if output_format == "json" else format_note_metadata(metadata)

        resource_client = ResourceClient(target.client, target.project.external_id)

        # --- Files that are not Markdown notes ---
        # Trigger: the resolved file is an image, PDF, JSON or other non-note file.
        # Why: slicing is defined over Markdown lines and headings only.
        # Outcome: the file is returned whole, in the form the client can display.
        if not runtime_file_path_is_markdown_note(note.file_path):
            if sliced:
                raise refusal(
                    "UNSUPPORTED",
                    f"'{note.file_path}' is not a Markdown note; section, lines and "
                    "max_tokens apply only to notes.",
                    try_instead=f"bm_read(identifier='{identifier}')",
                    output_format=output_format,
                )
            response = await resource_client.read(note.external_id)
            return _present_file(
                classify_file_response(response), note.file_path, note.title, output_format
            )

        # --- Markdown notes ---
        lines = f"{start_line or 1}-{'' if end_line is None else end_line}" if line_range else None
        payload: dict[str, Any] = dict(
            await read_note_json_by_external_id(
                knowledge_client=knowledge_client,
                resource_client=resource_client,
                entity_external_id=note.external_id,
                include_frontmatter=include_frontmatter,
                section=section,
                lines=lines,
                max_tokens=max_tokens,
            )
        )

    if not sliced:
        return payload if output_format == "json" else payload["content"]

    first, last, total = payload["start_line"], payload["end_line"], payload["total_lines"]
    if output_format == "json":
        width = last - first + 1
        payload["has_more"] = last < total
        payload["next_start_line"] = last + 1 if last < total else None
        payload["next_end_line"] = min(total, last + width) if last < total else None
        return payload
    # Trigger: max_tokens cut the content.
    # Why: the cut content ends with an ellipsis marker, so its lines no longer match
    #      the coordinates one for one and cannot be numbered.
    # Outcome: the coordinates go in the header and the content follows unnumbered.
    if payload.get("truncated"):
        return (
            f"Lines {first}-{last} of {total} (document, including frontmatter), cut at the "
            f"token budget\n{payload['content']}\n\n"
            f"(Continue with start_line={payload['continue_line']}.)"
        )
    return format_line_read(payload["content"], start_line=first, end_line=last, total_lines=total)


def _present_file(
    content: FileContent,
    file_path: str,
    title: str,
    output_format: OutputFormat,
) -> str | dict[str, Any] | ImageContent | EmbeddedResource:
    """Return a non-note file in the form an MCP client can display."""
    match content:
        case TextFile(text=text, content_type=content_type):
            if output_format == "json":
                return {
                    "title": title,
                    "file_path": file_path,
                    "content_type": content_type,
                    "content": text,
                }
            return text
        case ImageFile(jpeg=jpeg):
            return Image(data=jpeg, format="jpeg").to_image_content()
        case DocumentFile(data=data, content_type=content_type):
            document = File(data=data, name=PurePosixPath(file_path).name)
            return document.to_resource_content(mime_type=content_type)
        case UnsupportedImage():
            raise refusal(
                "UNSUPPORTED",
                f"'{file_path}' is an image in a format that cannot be shown; only PNG, "
                "JPEG and WebP are displayed.",
                output_format=output_format,
            )
        case DocumentTooLarge(size=size):  # pragma: no cover
            raise refusal(
                "UNSUPPORTED",
                f"'{file_path}' is {size} bytes, above the inline limit.",
                output_format=output_format,
            )
