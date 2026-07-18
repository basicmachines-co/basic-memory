"""Tests for semantic note timestamps on read-facing surfaces."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.api.v2.utils import to_search_results
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.services.directory_service import DirectoryService


class _EmptyEntityService:
    async def get_entities_by_id(self, ids: list[int]) -> Sequence[Any]:
        return ()


async def test_search_result_returns_indexed_semantic_timestamp() -> None:
    semantic_updated_at = datetime(2024, 1, 16, 11, 45, tzinfo=UTC)
    row = SearchIndexRow(
        project_id=7,
        id=42,
        entity_id=42,
        type="entity",
        title="Timestamped",
        permalink="notes/timestamped",
        file_path="notes/timestamped.md",
        created_at=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        updated_at=semantic_updated_at,
    )

    results = await to_search_results(_EmptyEntityService(), [row])

    assert results[0].updated_at == semantic_updated_at


def test_directory_result_returns_entity_semantic_timestamp() -> None:
    semantic_updated_at = datetime(2024, 1, 16, 11, 45, tzinfo=UTC)
    entity = Entity(
        id=42,
        external_id="note-42",
        project_id=7,
        title="Timestamped",
        note_type="note",
        content_type="text/markdown",
        permalink="notes/timestamped",
        file_path="notes/timestamped.md",
        created_at=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        updated_at=semantic_updated_at,
        mtime=1_800_000_000,
    )
    service = DirectoryService(
        cast(EntityRepository, object()),
        cast(async_sessionmaker[AsyncSession], object()),
    )

    tree = service._build_directory_tree_from_entities([entity], "/")

    assert tree.children[0].children[0].updated_at == semantic_updated_at


def test_search_index_row_normalizes_raw_naive_datetimes() -> None:
    row = SearchIndexRow(
        project_id=7,
        id=42,
        type="entity",
        file_path="notes/timestamped.md",
        created_at=datetime(2024, 1, 15, 10, 30),
        updated_at=datetime(2024, 1, 16, 11, 45),
    )

    assert row.created_at.utcoffset() is not None
    assert row.updated_at.utcoffset() is not None
