"""Tests for semantic_min_similarity threshold filtering in vector search."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.search_repository_base import (
    SMALL_NOTE_CONTENT_LIMIT,
    TOP_CHUNKS_PER_RESULT,
    SearchRepositoryBase,
)


@dataclass
class FakeRow:
    """Minimal stand-in for SearchIndexRow in threshold tests."""

    id: int
    type: str = "entity"
    score: float = 0.0
    matched_chunk_text: str | None = None
    content_snippet: str | None = None


class ConcreteSearchRepo(SearchRepositoryBase):
    """Minimal concrete subclass for testing base class threshold logic."""

    def __init__(self):
        # Skip super().__init__ — we only need the attributes under test
        self._semantic_enabled = True
        self._semantic_vector_k = 100
        self._semantic_min_similarity = 0.0
        self._embedding_provider = None
        self._vector_dimensions = 384
        self._vector_tables_initialized = True
        self.session_maker = None
        self.project_id = 1

    # --- Abstract method stubs (not exercised by these tests) ---

    async def init_search_index(self):
        pass  # pragma: no cover

    def _prepare_search_term(self, term, is_prefix=True):
        return term  # pragma: no cover

    async def search(self, **kwargs):
        return []  # pragma: no cover

    async def _ensure_vector_tables(self):
        pass  # pragma: no cover

    async def _run_vector_query(self, session, query_embedding, candidate_limit):
        return []  # pragma: no cover

    async def _write_embeddings(self, session, jobs, embeddings):
        pass  # pragma: no cover

    async def _delete_entity_chunks(self, session, entity_id):
        pass  # pragma: no cover

    async def _delete_stale_chunks(self, session, stale_ids, entity_id):
        pass  # pragma: no cover

    async def _update_timestamp_sql(self):
        return "CURRENT_TIMESTAMP"  # pragma: no cover

    def _distance_to_similarity(self, distance: float) -> float:
        return 1.0 / (1.0 + max(distance, 0.0))


def _make_vector_rows(scores: list[float]) -> list[dict]:
    """Build fake vector query rows with controlled distances.

    Distance = (1/score) - 1 inverts the similarity formula:
    similarity = 1 / (1 + distance)
    """
    rows = []
    for i, score in enumerate(scores):
        distance = (1.0 / score) - 1.0
        rows.append(
            {
                "chunk_key": f"entity:{i}:0",
                "best_distance": distance,
                "chunk_text": f"chunk text for entity:{i}:0",
            }
        )
    return rows


@asynccontextmanager
async def fake_scoped_session(session_maker):
    """Fake scoped_session that yields a mock session object."""
    yield AsyncMock()


COMMON_SEARCH_KWARGS = dict(
    search_text="test",
    permalink=None,
    permalink_match=None,
    title=None,
    note_types=None,
    after_date=None,
    search_item_types=None,
    metadata_filters=None,
    limit=10,
    offset=0,
)


@pytest.mark.asyncio
async def test_threshold_zero_returns_all():
    """With threshold=0.0 (default), all results pass through."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.0

    fake_rows = _make_vector_rows([0.9, 0.5, 0.3])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={i: FakeRow(id=i) for i in range(3)},
        ),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_threshold_filters_low_scores():
    """Results below the threshold are excluded."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.6

    # Scores: 0.9 (pass), 0.5 (fail), 0.3 (fail)
    fake_rows = _make_vector_rows([0.9, 0.5, 0.3])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            # Only entity_0 (score=0.9) passes the threshold; the fetch only gets id 0
            return_value={0: FakeRow(id=0)},
        ),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    # Only the 0.9 result passes the 0.6 threshold
    assert len(results) == 1


@pytest.mark.asyncio
async def test_threshold_returns_empty_when_all_below():
    """All results below threshold → empty list, no DB fetch."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.8

    # All scores below 0.8
    fake_rows = _make_vector_rows([0.5, 0.4, 0.3])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    mock_fetch = AsyncMock()

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(repo, "_fetch_search_index_rows_by_ids", mock_fetch),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    assert results == []
    # Should short-circuit before fetching search_index rows
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_per_query_min_similarity_overrides_instance_default():
    """Per-query min_similarity takes precedence over instance-level default."""
    repo = ConcreteSearchRepo()
    # Instance default would filter out 0.5 and 0.3
    repo._semantic_min_similarity = 0.6

    # Scores: 0.9, 0.5, 0.3
    fake_rows = _make_vector_rows([0.9, 0.5, 0.3])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={i: FakeRow(id=i) for i in range(3)},
        ),
    ):
        # Override to 0.0 → all results pass through despite instance default of 0.6
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS, min_similarity=0.0)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_per_query_min_similarity_tightens_threshold():
    """Per-query min_similarity=0.8 filters more aggressively than instance default."""
    repo = ConcreteSearchRepo()
    # Instance default is permissive
    repo._semantic_min_similarity = 0.0

    # Scores: 0.9, 0.5, 0.3
    fake_rows = _make_vector_rows([0.9, 0.5, 0.3])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            # Only id=0 (score=0.9) will be fetched after filtering
            return_value={0: FakeRow(id=0)},
        ),
    ):
        # Override to 0.8 → only score=0.9 passes
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS, min_similarity=0.8)

    assert len(results) == 1
    assert results[0].id == 0


@pytest.mark.asyncio
async def test_matched_chunk_text_populated_on_vector_results():
    """Vector search results carry the matched chunk text from the best-matching chunk."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.0

    fake_rows = _make_vector_rows([0.9, 0.7])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={i: FakeRow(id=i) for i in range(2)},
        ),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    assert len(results) == 2
    # Results are sorted by score descending, so id=0 (0.9) first, id=1 (0.7) second
    # Each entity has only 1 chunk and no content_snippet, so chunk text is used directly
    assert results[0].matched_chunk_text == "chunk text for entity:0:0"
    assert results[1].matched_chunk_text == "chunk text for entity:1:0"


def _make_multi_chunk_vector_rows(si_id: int, scores: list[float]) -> list[dict]:
    """Build multiple fake vector chunks for a single search_index row.

    Each chunk gets a unique chunk_index within the same si_id.
    Distance = (1/score) - 1 inverts the similarity formula.
    """
    rows = []
    for chunk_idx, score in enumerate(scores):
        distance = (1.0 / score) - 1.0
        rows.append(
            {
                "chunk_key": f"entity:{si_id}:{chunk_idx}",
                "best_distance": distance,
                "chunk_text": f"chunk-{chunk_idx} (sim={score})",
            }
        )
    return rows


@pytest.mark.asyncio
async def test_top_n_chunks_joined_in_matched_chunk_text():
    """Large note with 7 chunks: top 5 by similarity are joined with separator."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.0

    # 7 chunks for entity 0, with varying similarities
    chunk_scores = [0.6, 0.9, 0.4, 0.8, 0.75, 0.3, 0.85]
    fake_rows = _make_multi_chunk_vector_rows(si_id=0, scores=chunk_scores)

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    # content_snippet exceeds SMALL_NOTE_CONTENT_LIMIT → top-N chunks path
    large_content = "x" * (SMALL_NOTE_CONTENT_LIMIT + 1)

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={0: FakeRow(id=0, content_snippet=large_content)},
        ),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    assert len(results) == 1
    text = results[0].matched_chunk_text

    # Top 5 chunks by similarity: 0.9, 0.85, 0.8, 0.75, 0.6 (0.4 and 0.3 excluded)
    parts = text.split("\n---\n")
    assert len(parts) == TOP_CHUNKS_PER_RESULT
    assert parts[0] == "chunk-1 (sim=0.9)"
    assert parts[1] == "chunk-6 (sim=0.85)"
    assert parts[2] == "chunk-3 (sim=0.8)"
    assert parts[3] == "chunk-4 (sim=0.75)"
    assert parts[4] == "chunk-0 (sim=0.6)"


@pytest.mark.asyncio
async def test_small_note_returns_full_content_as_matched_chunk():
    """Small note (content_snippet under limit) returns full content instead of chunks."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.0

    fake_rows = _make_vector_rows([0.9])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    small_content = "This is a short note with all the important details."
    assert len(small_content) <= SMALL_NOTE_CONTENT_LIMIT

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={0: FakeRow(id=0, content_snippet=small_content)},
        ),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    assert len(results) == 1
    # Full content returned instead of the chunk text
    assert results[0].matched_chunk_text == small_content


@pytest.mark.asyncio
async def test_large_note_returns_chunks_not_full_content():
    """Large note (content_snippet over limit) returns top-N chunks, not full content."""
    repo = ConcreteSearchRepo()
    repo._semantic_min_similarity = 0.0

    fake_rows = _make_vector_rows([0.9])

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    large_content = "x" * (SMALL_NOTE_CONTENT_LIMIT + 500)

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={0: FakeRow(id=0, content_snippet=large_content)},
        ),
    ):
        results = await repo._search_vector_only(**COMMON_SEARCH_KWARGS)

    assert len(results) == 1
    # Should use chunk text, not the full content
    assert results[0].matched_chunk_text == "chunk text for entity:0:0"
    assert results[0].matched_chunk_text != large_content
