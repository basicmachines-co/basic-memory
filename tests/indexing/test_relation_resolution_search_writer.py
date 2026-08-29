"""Tests for per-entity relation search refresh outcomes."""

from unittest.mock import AsyncMock, Mock

import pytest

from basic_memory.indexing.batch_indexer import RelationResolutionSearchWriter
from basic_memory.indexing.relation_resolution import RelationSearchRefreshResult


@pytest.mark.asyncio
async def test_relation_search_writer_skips_only_missing_entity_content() -> None:
    first = Mock(id=1)
    missing = Mock(id=2)
    last = Mock(id=3)
    search_writer = Mock()
    search_writer.index_entity_data = AsyncMock(
        side_effect=[None, FileNotFoundError("missing object"), None]
    )

    result = await RelationResolutionSearchWriter(search_writer).index_entities(
        [last, missing, first],
        content_by_entity_id={1: "first", 3: "last"},
    )

    assert result == RelationSearchRefreshResult(missing_content_entity_ids=frozenset({2}))
    assert [call.args[0].id for call in search_writer.index_entity_data.await_args_list] == [
        1,
        2,
        3,
    ]


@pytest.mark.asyncio
async def test_relation_search_writer_preserves_unexpected_failures() -> None:
    search_writer = Mock()
    search_writer.index_entity_data = AsyncMock(side_effect=RuntimeError("search unavailable"))

    with pytest.raises(RuntimeError, match="search unavailable"):
        await RelationResolutionSearchWriter(search_writer).index_entities(
            [Mock(id=1)],
            content_by_entity_id={},
        )
