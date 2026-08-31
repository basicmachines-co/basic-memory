"""Reusable typed note-read shaping for MCP adapters."""

from typing import Any, NotRequired, Protocol, TypedDict

import logfire
import yaml
from httpx import Response

from basic_memory.schemas.v2 import EntityResponseV2


class ReadNoteJsonPayload(TypedDict):
    """Successful ``read_note(output_format="json")`` payload.

    The slice keys appear only on sliced reads (section/lines/max_tokens), whose
    ``content`` is the server-computed slice with document-absolute coordinates;
    ``truncated``/``continue_line`` appear only when max_tokens cut the content.
    """

    title: str
    permalink: str | None
    file_path: str
    content: str
    frontmatter: dict[str, Any] | None
    section: NotRequired[str | None]
    start_line: NotRequired[int]
    end_line: NotRequired[int]
    total_lines: NotRequired[int]
    truncated: NotRequired[bool]
    continue_line: NotRequired[int]


class KnowledgeEntityReader(Protocol):
    """Entity-read capability required by exact-ID note reads."""

    async def get_entity(
        self,
        entity_id: str,
        *,
        section: str | None = None,
        lines: str | None = None,
        max_tokens: int | None = None,
    ) -> EntityResponseV2:
        """Return the entity response for one exact external ID, optionally sliced."""


class NoteResourceReader(Protocol):
    """Resource-read capability used only for legacy entities without content."""

    async def read(self, entity_id: str) -> Response:
        """Return raw resource content for one exact external ID."""


def parse_opening_frontmatter(content: str) -> tuple[str, dict[str, Any] | None]:
    """Parse opening YAML frontmatter and return ``(body, frontmatter)``.

    Mirrors CLI behavior: only parses a frontmatter block at the very top.
    If parsing fails or frontmatter is not a mapping, returns body unchanged and ``None``.
    """
    original_content = content
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return original_content, None

    closing_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return original_content, None

    frontmatter_text = "".join(lines[1:closing_index])
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        return original_content, None

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return original_content, None

    body_content = "".join(lines[closing_index + 1 :])
    return body_content, parsed


async def read_note_json_by_external_id(
    *,
    knowledge_client: KnowledgeEntityReader,
    resource_client: NoteResourceReader,
    entity_external_id: str,
    include_frontmatter: bool = False,
    section: str | None = None,
    lines: str | None = None,
    max_tokens: int | None = None,
) -> ReadNoteJsonPayload:
    """Read and shape one note by exact external ID without identifier resolution.

    The entity response carries the accepted Markdown and its routing metadata. Legacy or
    non-note entities may not carry content, so only that explicit ``None`` state falls back to
    the raw resource route. Empty accepted Markdown remains a valid response and never triggers
    a speculative resource read.

    A sliced read (``section``/``lines``/``max_tokens``) never falls back: the server 404s
    content-less entities and returns the slice with its document-absolute coordinates. Slices
    carry no frontmatter block, so ``include_frontmatter`` does not apply to them.
    """
    with logfire.span(
        "mcp.read_note.shape_response",
        domain="mcp",
        action="read_note",
        phase="shape_response",
    ) as span:
        # Plain reads keep the bare positional call so the unsliced path stays
        # byte-for-byte identical (and existing duck-typed readers unaffected).
        slice_requested = section is not None or lines is not None or max_tokens is not None
        if slice_requested:
            entity = await knowledge_client.get_entity(
                entity_external_id, section=section, lines=lines, max_tokens=max_tokens
            )
            span.set_attribute("read_note.resource_fallback", False)
            if (
                entity.content is None
                or entity.content_start_line is None
                or entity.content_end_line is None
                or entity.content_total_lines is None
            ):  # pragma: no cover — the server always returns coordinates for sliced reads
                raise ValueError("sliced note read response is missing slice coordinates")
            payload: ReadNoteJsonPayload = {
                "title": entity.title,
                "permalink": entity.permalink,
                "file_path": entity.file_path,
                "content": entity.content,
                "frontmatter": None,
                "section": entity.section,
                "start_line": entity.content_start_line,
                "end_line": entity.content_end_line,
                "total_lines": entity.content_total_lines,
            }
            # The server sets continue_line exactly when max_tokens truncated the
            # content, so one narrowing check carries both keys.
            continue_line = entity.content_continue_line
            if continue_line is not None:
                payload["truncated"] = True
                payload["continue_line"] = continue_line
            return payload

        entity = await knowledge_client.get_entity(entity_external_id)
        content_text = entity.content
        resource_fallback = content_text is None
        if resource_fallback:
            response = await resource_client.read(entity_external_id)
            content_text = response.text

        span.set_attribute("read_note.resource_fallback", resource_fallback)
        body_content, parsed_frontmatter = parse_opening_frontmatter(content_text)
        return {
            "title": entity.title,
            "permalink": entity.permalink,
            "file_path": entity.file_path,
            "content": content_text if include_frontmatter else body_content,
            "frontmatter": parsed_frontmatter,
        }
