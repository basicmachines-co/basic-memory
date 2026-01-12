"""Integration tests for PostgresSearchRepository.

These tests only run in Postgres mode (testcontainers) and ensure that the
Postgres tsvector-backed search implementation remains well covered.
"""

from datetime import datetime, timedelta, timezone

import pytest

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchItemType


pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _require_postgres_backend(db_backend):
    """Ensure these tests never run under SQLite."""
    if db_backend != "postgres":
        pytest.skip("PostgresSearchRepository tests require BASIC_MEMORY_TEST_POSTGRES=1")


@pytest.mark.asyncio
async def test_postgres_search_repository_index_and_search(session_maker, test_project):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    await repo.init_search_index()  # no-op but should be exercised

    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=test_project.id,
        id=1,
        title="Coffee Brewing",
        content_stems="coffee brewing pour over",
        content_snippet="coffee brewing snippet",
        permalink="docs/coffee-brewing",
        file_path="docs/coffee-brewing.md",
        type="entity",
        metadata={"entity_type": "note"},
        created_at=now,
        updated_at=now,
    )
    await repo.index_item(row)

    # Basic full-text search
    results = await repo.search(search_text="coffee")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Boolean query path
    results = await repo.search(search_text="coffee AND brewing")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Title-only search path
    results = await repo.search(title="Coffee Brewing")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Exact permalink search
    results = await repo.search(permalink="docs/coffee-brewing")
    assert len(results) == 1

    # Permalink pattern match (LIKE)
    results = await repo.search(permalink_match="docs/coffee*")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Item type filter
    results = await repo.search(search_item_types=[SearchItemType.ENTITY])
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Entity type filter via metadata JSONB containment
    results = await repo.search(types=["note"])
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Date filter (also exercises order_by_clause)
    results = await repo.search(after_date=now - timedelta(days=1))
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Limit/offset
    results = await repo.search(limit=1, offset=0)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_postgres_search_repository_bulk_index_items_and_prepare_terms(
    session_maker, test_project
):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    # Empty batch is a no-op
    await repo.bulk_index_items([])

    # Exercise term preparation helpers
    assert "&" in repo._prepare_search_term("coffee AND brewing")
    assert repo._prepare_search_term("coff*") == "coff:*"
    assert repo._prepare_search_term("()&!:") == "NOSPECIALCHARS:*"
    assert repo._prepare_search_term("coffee brewing") == "coffee:* & brewing:*"
    assert repo._prepare_single_term("   ") == "   "
    assert repo._prepare_single_term("coffee", is_prefix=False) == "coffee"

    now = datetime.now(timezone.utc)
    rows = [
        SearchIndexRow(
            project_id=test_project.id,
            id=10,
            title="Pour Over",
            content_stems="pour over coffee",
            content_snippet="pour over snippet",
            permalink="docs/pour-over",
            file_path="docs/pour-over.md",
            type="entity",
            metadata={"entity_type": "note"},
            created_at=now,
            updated_at=now,
        ),
        SearchIndexRow(
            project_id=test_project.id,
            id=11,
            title="French Press",
            content_stems="french press coffee",
            content_snippet="french press snippet",
            permalink="docs/french-press",
            file_path="docs/french-press.md",
            type="entity",
            metadata={"entity_type": "note"},
            created_at=now,
            updated_at=now,
        ),
    ]

    await repo.bulk_index_items(rows)

    results = await repo.search(search_text="coffee")
    permalinks = {r.permalink for r in results}
    assert "docs/pour-over" in permalinks
    assert "docs/french-press" in permalinks


@pytest.mark.asyncio
async def test_postgres_search_repository_wildcard_text_and_permalink_match_exact(
    session_maker, test_project
):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    now = datetime.now(timezone.utc)
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=1,
            title="X",
            content_stems="x",
            content_snippet="x",
            permalink="docs/x",
            file_path="docs/x.md",
            type="entity",
            metadata={"entity_type": "note"},
            created_at=now,
            updated_at=now,
        )
    )

    # search_text="*" should not add tsquery conditions (covers the pass branch)
    results = await repo.search(search_text="*")
    assert results

    # permalink_match without '*' uses exact match branch
    results = await repo.search(permalink_match="docs/x")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_postgres_search_repository_tsquery_syntax_error_returns_empty(
    session_maker, test_project
):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    # Trailing boolean operator creates an invalid tsquery; repository should return []
    results = await repo.search(search_text="coffee AND")
    assert results == []


@pytest.mark.asyncio
async def test_postgres_search_repository_reraises_non_tsquery_db_errors(
    session_maker, test_project
):
    """Dropping the search_index table triggers a non-tsquery DB error which should be re-raised."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    from sqlalchemy import text
    from basic_memory import db

    async with db.scoped_session(session_maker) as session:
        await session.execute(text("DROP TABLE search_index"))
        await session.commit()

    with pytest.raises(Exception):
        # Use a non-text query so the generated SQL doesn't include to_tsquery(),
        # ensuring we hit the generic "re-raise other db errors" branch.
        await repo.search(permalink="docs/anything")
