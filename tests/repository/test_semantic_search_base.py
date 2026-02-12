"""Tests for shared semantic search logic in SearchRepositoryBase.

Covers: _compose_row_source_text, _split_text_into_chunks, _build_chunk_records,
_search_hybrid entity_id fusion key, and SemanticSearchDisabledError in SQLite.
"""

from types import SimpleNamespace

import pytest

from basic_memory.repository.search_repository_base import (
    MAX_VECTOR_CHUNK_CHARS,
    SearchRepositoryBase,
)
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.repository.semantic_errors import SemanticSearchDisabledError
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


# --- Helpers ---


def _make_row(
    *,
    row_type: str,
    title: str = "Test Title",
    permalink: str = "test/permalink",
    content_stems: str = "",
    content_snippet: str = "",
    category: str = "",
    relation_type: str = "",
    row_id: int = 1,
):
    """Create a SimpleNamespace mimicking a search_index DB row."""
    return SimpleNamespace(
        id=row_id,
        type=row_type,
        title=title,
        permalink=permalink,
        content_stems=content_stems,
        content_snippet=content_snippet,
        category=category,
        relation_type=relation_type,
    )


class _ConcreteRepo(SearchRepositoryBase):
    """Minimal concrete subclass for testing base class methods."""

    _semantic_enabled = False
    _semantic_vector_k = 100
    _embedding_provider = None
    _vector_dimensions = 4
    _vector_tables_initialized = False

    def __init__(self):
        # Bypass parent __init__ since we don't need a real session_maker for unit tests
        self.session_maker = None
        self.project_id = 1

    async def init_search_index(self):
        pass

    def _prepare_search_term(self, term, is_prefix=True):
        return term

    async def search(self, **kwargs):
        return []

    async def _ensure_vector_tables(self):
        pass

    async def _run_vector_query(self, session, query_embedding, candidate_limit):
        return []

    async def _write_embeddings(self, session, jobs, embeddings):
        pass

    async def _delete_entity_chunks(self, session, entity_id):
        pass

    async def _delete_stale_chunks(self, session, stale_ids, entity_id):
        pass

    async def _update_timestamp_sql(self):
        return "CURRENT_TIMESTAMP"


# --- _compose_row_source_text ---


class TestComposeRowSourceText:
    """Verify _compose_row_source_text produces correct text for all row types."""

    def setup_method(self):
        self.repo = _ConcreteRepo()

    def test_entity_row_uses_content_snippet_not_content_stems(self):
        """Entity rows should use content_snippet (human-readable) instead of content_stems."""
        row = _make_row(
            row_type=SearchItemType.ENTITY.value,
            title="Auth Design",
            permalink="specs/auth-design",
            content_stems="auth login token session stems expanded variants",
            content_snippet="JWT authentication with session management",
        )
        result = self.repo._compose_row_source_text(row)
        assert "Auth Design" in result
        assert "specs/auth-design" in result
        assert "JWT authentication with session management" in result
        # content_stems should NOT appear
        assert "stems expanded variants" not in result

    def test_observation_row_includes_category(self):
        row = _make_row(
            row_type=SearchItemType.OBSERVATION.value,
            title="Coffee Notes",
            permalink="notes/coffee",
            category="technique",
            content_snippet="Pour over produces cleaner cups",
        )
        result = self.repo._compose_row_source_text(row)
        assert "Coffee Notes" in result
        assert "technique" in result
        assert "Pour over produces cleaner cups" in result

    def test_relation_row_includes_relation_type(self):
        row = _make_row(
            row_type=SearchItemType.RELATION.value,
            title="Brewing",
            permalink="notes/brewing",
            relation_type="relates_to",
            content_snippet="Coffee brewing method",
        )
        result = self.repo._compose_row_source_text(row)
        assert "Brewing" in result
        assert "relates_to" in result
        assert "Coffee brewing method" in result

    def test_entity_row_with_none_fields(self):
        """Null fields should be skipped, not included as empty strings."""
        row = _make_row(
            row_type=SearchItemType.ENTITY.value,
            title="Minimal",
            permalink="",
            content_snippet="",
        )
        row.permalink = None
        row.content_snippet = None
        result = self.repo._compose_row_source_text(row)
        assert result == "Minimal"


# --- _split_text_into_chunks ---


class TestSplitTextIntoChunks:
    """Verify markdown-aware text splitting."""

    def setup_method(self):
        self.repo = _ConcreteRepo()

    def test_short_text_returns_single_chunk(self):
        result = self.repo._split_text_into_chunks("Short text")
        assert result == ["Short text"]

    def test_empty_text_returns_empty(self):
        assert self.repo._split_text_into_chunks("") == []
        assert self.repo._split_text_into_chunks("   ") == []

    def test_splits_on_headers(self):
        # Make it long enough to actually split
        long_section_a = "## Section A\n" + ("A content. " * 100)
        long_section_b = "## Section B\n" + ("B content. " * 100)
        long_text = f"Intro paragraph\n\n{long_section_a}\n\n{long_section_b}"
        result = self.repo._split_text_into_chunks(long_text)
        assert len(result) >= 2

    def test_paragraph_merging_within_limit(self):
        """Paragraphs shorter than limit should be merged."""
        para1 = "First paragraph."
        para2 = "Second paragraph."
        text = f"# Header\n\n{para1}\n\n{para2}"
        result = self.repo._split_text_into_chunks(text)
        # Both paragraphs fit in one chunk
        assert len(result) == 1
        assert para1 in result[0]
        assert para2 in result[0]

    def test_long_paragraph_uses_char_window(self):
        """A single paragraph longer than MAX_VECTOR_CHUNK_CHARS uses sliding window."""
        long_para = "x" * (MAX_VECTOR_CHUNK_CHARS * 3)
        result = self.repo._split_text_into_chunks(long_para)
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= MAX_VECTOR_CHUNK_CHARS


# --- _build_chunk_records ---


class TestBuildChunkRecords:
    def setup_method(self):
        self.repo = _ConcreteRepo()

    def test_produces_records_with_correct_keys(self):
        rows = [
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Test",
                permalink="test",
                content_snippet="content",
                row_id=42,
            )
        ]
        records = self.repo._build_chunk_records(rows)
        assert len(records) >= 1
        for record in records:
            assert "chunk_key" in record
            assert "chunk_text" in record
            assert "source_hash" in record
            assert record["chunk_key"].startswith("entity:")

    def test_chunk_key_includes_row_id(self):
        rows = [
            _make_row(
                row_type=SearchItemType.OBSERVATION.value,
                content_snippet="obs content",
                row_id=99,
            )
        ]
        records = self.repo._build_chunk_records(rows)
        assert any("99" in r["chunk_key"] for r in records)


# --- SQLite SemanticSearchDisabledError ---


@pytest.mark.asyncio
async def test_sqlite_vector_search_raises_disabled_error(search_repository):
    """Vector mode on SQLite should raise SemanticSearchDisabledError when disabled."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific test.")

    search_repository._semantic_enabled = False
    with pytest.raises(SemanticSearchDisabledError):
        await search_repository.search(
            search_text="test query",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            limit=5,
            offset=0,
        )


@pytest.mark.asyncio
async def test_sqlite_hybrid_search_raises_disabled_error(search_repository):
    """Hybrid mode on SQLite should raise SemanticSearchDisabledError when disabled."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific test.")

    search_repository._semantic_enabled = False
    with pytest.raises(SemanticSearchDisabledError):
        await search_repository.search(
            search_text="test query",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=5,
            offset=0,
        )
