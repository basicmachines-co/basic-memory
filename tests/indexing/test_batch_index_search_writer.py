"""Tests for batch-index search writer adapters."""

from __future__ import annotations

import pytest

from basic_memory.indexing import MarkdownOnlyIndexEntitySearchWriter
from basic_memory.models import Entity


class RecordingIndexEntitySearchWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[Entity, str | None]] = []

    async def index_entity_data(self, entity: Entity, content: str | None = None) -> None:
        self.calls.append((entity, content))


def entity_with_content_type(content_type: str) -> Entity:
    return Entity(
        title="Indexed file",
        note_type="note",
        content_type=content_type,
        project_id=1,
        file_path="notes/indexed.md",
    )


@pytest.mark.asyncio
async def test_markdown_only_search_writer_indexes_markdown_entities() -> None:
    search_writer = RecordingIndexEntitySearchWriter()
    entity = entity_with_content_type("text/markdown")

    await MarkdownOnlyIndexEntitySearchWriter(search_writer).index_entity_data(
        entity,
        content="# Indexed",
    )

    assert search_writer.calls == [(entity, "# Indexed")]


@pytest.mark.asyncio
async def test_markdown_only_search_writer_skips_regular_files() -> None:
    search_writer = RecordingIndexEntitySearchWriter()
    entity = entity_with_content_type("application/pdf")

    await MarkdownOnlyIndexEntitySearchWriter(search_writer).index_entity_data(
        entity,
        content=None,
    )

    assert search_writer.calls == []
