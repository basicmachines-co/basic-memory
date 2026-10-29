"""read_note_json_by_external_id: one entity read, the resource only when content is absent."""

from datetime import UTC, datetime

import pytest
from httpx import Response

from basic_memory.mcp.note_reads import (
    format_note_metadata,
    parse_opening_frontmatter,
    read_note_json_by_external_id,
)
from basic_memory.schemas.v2 import EntityResponseV2

ENTITY_ID = "22222222-2222-4222-8222-222222222222"


def _entity(
    *, content: str | None = "---\ntitle: Request Count\nstatus: ready\n---\nBody\n"
) -> EntityResponseV2:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    return EntityResponseV2(
        external_id=ENTITY_ID,
        id=1,
        title="Request Count",
        note_type="note",
        permalink="notes/request-count",
        file_path="notes/Request Count.md",
        content=content,
        entity_metadata={"title": "Indexed Title", "status": "indexed"},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted_content", ["accepted content", ""])
async def test_exact_id_helper_does_not_read_resource_for_present_content(
    accepted_content: str,
) -> None:
    class EntityReader:
        calls = 0

        async def get_entity(
            self,
            entity_id: str,
            *,
            section: str | None = None,
            lines: str | None = None,
            max_tokens: int | None = None,
        ) -> EntityResponseV2:
            assert section is None and lines is None and max_tokens is None
            self.calls += 1
            assert entity_id == ENTITY_ID
            return _entity(content=accepted_content)

    class ResourceReader:
        calls = 0

        async def read(self, entity_id: str) -> Response:
            del entity_id
            self.calls += 1
            raise AssertionError("present content must not fall back to resource")

    entity_reader = EntityReader()
    resource_reader = ResourceReader()
    result = await read_note_json_by_external_id(
        knowledge_client=entity_reader,
        resource_client=resource_reader,
        entity_external_id=ENTITY_ID,
    )

    assert result["content"] == accepted_content
    assert entity_reader.calls == 1
    assert resource_reader.calls == 0


@pytest.mark.asyncio
async def test_exact_id_helper_reads_resource_once_only_when_content_is_absent() -> None:
    class EntityReader:
        calls = 0

        async def get_entity(
            self,
            entity_id: str,
            *,
            section: str | None = None,
            lines: str | None = None,
            max_tokens: int | None = None,
        ) -> EntityResponseV2:
            assert section is None and lines is None and max_tokens is None
            self.calls += 1
            assert entity_id == ENTITY_ID
            return _entity(content=None)

    class ResourceReader:
        calls = 0

        async def read(self, entity_id: str) -> Response:
            self.calls += 1
            assert entity_id == ENTITY_ID
            return Response(200, text="---\nlegacy: true\n---\nlegacy body\n")

    entity_reader = EntityReader()
    resource_reader = ResourceReader()
    result = await read_note_json_by_external_id(
        knowledge_client=entity_reader,
        resource_client=resource_reader,
        entity_external_id=ENTITY_ID,
    )

    assert result["content"].strip() == "legacy body"
    assert result["frontmatter"] == {"legacy": True}
    assert entity_reader.calls == 1
    assert resource_reader.calls == 1


@pytest.mark.asyncio
async def test_exact_id_helper_does_not_fabricate_frontmatter_from_entity_metadata() -> None:
    class EntityReader:
        async def get_entity(
            self,
            entity_id: str,
            *,
            section: str | None = None,
            lines: str | None = None,
            max_tokens: int | None = None,
        ) -> EntityResponseV2:
            assert section is None and lines is None and max_tokens is None
            assert entity_id == ENTITY_ID
            return _entity(content="plain body\n")

    class ResourceReader:
        async def read(self, entity_id: str) -> Response:
            del entity_id
            raise AssertionError("present content must not fall back to resource")

    result = await read_note_json_by_external_id(
        knowledge_client=EntityReader(),
        resource_client=ResourceReader(),
        entity_external_id=ENTITY_ID,
    )

    assert result["content"] == "plain body\n"
    assert result["frontmatter"] is None


def test_parse_opening_frontmatter_handles_crlf():
    """JSON note reads strip frontmatter from Windows-written markdown."""
    body, frontmatter = parse_opening_frontmatter(
        "---\r\ntitle: Windows Note\r\ntype: note\r\n---\r\n\r\nBody text\r\n"
    )

    assert frontmatter == {"title": "Windows Note", "type": "note"}
    assert body.strip() == "Body text"


def test_format_note_metadata_prints_the_full_checksum_and_marks_missing_values():
    """The text metadata read never truncates the checksum, and says when it has none."""
    checksum = "c" * 64
    text = format_note_metadata(
        {"title": "T", "permalink": None, "file_path": "t.md", "checksum": checksum}
    )
    assert text.splitlines() == [
        "# Note metadata",
        "title: T",
        "permalink: unknown",
        "file_path: t.md",
        f"checksum: {checksum}",
    ]
