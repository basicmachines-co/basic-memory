"""Reusable typed note-read shaping for MCP adapters."""

from typing import Any, Protocol, TypedDict

import logfire
import yaml
from httpx import Response

from basic_memory.schemas.v2 import EntityResponseV2


class ReadNoteJsonPayload(TypedDict):
    """Existing successful ``read_note(output_format="json")`` payload."""

    title: str
    permalink: str | None
    file_path: str
    content: str
    frontmatter: dict[str, Any] | None


class KnowledgeEntityReader(Protocol):
    """Entity-read capability required by exact-ID note reads."""

    async def get_entity(self, entity_id: str) -> EntityResponseV2:
        """Return the entity response for one exact external ID."""


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
) -> ReadNoteJsonPayload:
    """Read and shape one note by exact external ID without identifier resolution.

    The entity response carries the accepted Markdown and its routing metadata. Legacy or
    non-note entities may not carry content, so only that explicit ``None`` state falls back to
    the raw resource route. Empty accepted Markdown remains a valid response and never triggers
    a speculative resource read.
    """
    with logfire.span(
        "mcp.read_note.shape_response",
        domain="mcp",
        action="read_note",
        phase="shape_response",
    ) as span:
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
