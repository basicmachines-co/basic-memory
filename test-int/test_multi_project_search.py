"""One database pipeline over real SQLite/sqlite-vec or Postgres/pgvector storage.

Run with BASIC_MEMORY_TEST_POSTGRES=1 for Postgres. Only the embedding provider
is deterministic; indexing, vector persistence, retrieval, and API hydration are real.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.deps.read_cache import get_read_cache
from basic_memory.models import Entity, Project
from basic_memory.repository.multi_project_search_repository import MultiProjectSearchRepository
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode
from basic_memory.services.search_service import SearchService


class CountingEmbeddingProvider:
    model_name = "scope-test"
    dimensions = 4

    def __init__(self) -> None:
        self.query_calls = 0

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


@dataclass
class Corpus:
    session_maker: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    config: BasicMemoryConfig
    projects: list[Project]
    provider: CountingEmbeddingProvider

    def repository(self, project_ids: Sequence[int]) -> MultiProjectSearchRepository:
        return MultiProjectSearchRepository(
            self.session_maker,
            project_ids,
            app_config=self.config,
            embedding_provider=self.provider,
        )


@pytest.fixture
async def corpus(
    engine_factory, app_config: BasicMemoryConfig, monkeypatch: pytest.MonkeyPatch
) -> Corpus:
    engine, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    app_config.reranker_enabled = False
    app_config.semantic_min_similarity = 0.0
    provider = CountingEmbeddingProvider()
    from basic_memory.api.v2.routers import multi_project_search_router

    monkeypatch.setattr(
        multi_project_search_router, "create_embedding_provider", lambda _config: provider
    )
    projects = []
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for index in range(3):
        async with db.scoped_session(session_maker) as session:
            project = Project(
                name=f"scope-{index}", permalink=f"scope-{index}", path=f"/scope-{index}"
            )
            session.add(project)
            await session.flush()
            entity = Entity(
                project_id=project.id,
                title="shared nebula",
                note_type="note",
                content_type="text/markdown",
                permalink="notes/shared",
                file_path="notes/shared.md",
                entity_metadata={"status": "open" if index == 0 else "closed", "tags": ["scope"]},
                created_at=now,
                updated_at=now,
            )
            session.add(entity)
            await session.commit()
        projects.append(project)
        repo_type = (
            PostgresSearchRepository
            if app_config.database_backend == DatabaseBackend.POSTGRES
            else SQLiteSearchRepository
        )
        writer = repo_type(
            session_maker, project.id, app_config=app_config, embedding_provider=provider
        )
        await writer.init_search_index()
        # Search rows have a composite (project, type, id) identity. Deliberately
        # collide the observation/relation IDs and paths across all three projects.
        rows = [
            SearchIndexRow(
                project_id=project.id,
                id=entity.id,
                entity_id=entity.id,
                type="entity",
                file_path=entity.file_path,
                permalink=entity.permalink,
                title=entity.title,
                content_stems="shared nebula",
                content_snippet="shared nebula",
                metadata={"note_type": "note"},
                created_at=now,
                updated_at=now,
            )
        ]
        for row_type in ("observation", "relation"):
            rows.append(
                SearchIndexRow(
                    project_id=project.id,
                    id=500,
                    entity_id=entity.id,
                    type=row_type,
                    file_path=entity.file_path,
                    permalink=f"notes/shared/{row_type}",
                    title="shared nebula",
                    content_stems="shared nebula",
                    content_snippet=f"shared nebula project {index} {row_type}",
                    category="fact" if row_type == "observation" else None,
                    from_id=entity.id if row_type == "relation" else None,
                    relation_type="relates_to" if row_type == "relation" else None,
                    created_at=now,
                    updated_at=now,
                )
            )
        await writer.bulk_index_items(rows)
        await writer.sync_entity_vectors(entity.id)
    return Corpus(session_maker, engine, app_config, projects, provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
@pytest.mark.parametrize("scope_size", [0, 1, 2])
async def test_explicit_scope_one_pipeline_and_read_only(
    corpus: Corpus, mode: SearchRetrievalMode, scope_size: int
) -> None:
    ids = [project.id for project in corpus.projects[:scope_size]]
    repo = corpus.repository(ids)
    prepared = SearchService.prepare_query(SearchQuery(text="nebula", retrieval_mode=mode))
    assert prepared is not None
    statements: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(corpus.engine.sync_engine, "before_cursor_execute", record)
    try:
        rows = await repo.search(prepared, limit=100)
    finally:
        event.remove(corpus.engine.sync_engine, "before_cursor_execute", record)
    assert len(rows) == scope_size * 3
    assert {row.project_id for row in rows} == set(ids)
    assert len({(row.project_id, row.type, row.id) for row in rows}) == len(rows)
    assert corpus.provider.query_calls == int(scope_size > 0 and mode != SearchRetrievalMode.FTS)
    retrievals = [
        sql for sql in statements if "search_index" in sql and not sql.startswith("PRAGMA")
    ]
    assert len(retrievals) == int(scope_size > 0)
    assert all(
        not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP"))
        for sql in statements
    )
    assert not hasattr(repo, "index_item") and not hasattr(repo, "delete_by_entity_id")
    if mode == SearchRetrievalMode.FTS:
        assert await repo.count(prepared) == scope_size * 3
    else:
        assert all(row.matched_chunk_text for row in rows)
        with pytest.raises(ValueError, match="Exact counts"):
            await repo.count(prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_global_pagination_is_independent_of_page_size(
    corpus: Corpus, mode: SearchRetrievalMode
) -> None:
    repo = corpus.repository([project.id for project in corpus.projects[:2]])
    query = SearchService.prepare_query(SearchQuery(text="nebula", retrieval_mode=mode))
    assert query is not None
    complete = await repo.search(query, limit=100)
    pages = [
        row
        for offset in range(0, len(complete), 2)
        for row in await repo.search(query, limit=2, offset=offset)
    ]
    assert [(r.project_id, r.type, r.id, r.score) for r in pages] == [
        (r.project_id, r.type, r.id, r.score) for r in complete
    ]
    assert await repo.search(query, offset=100) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_filters_scope_counts_and_api_hydration(
    corpus: Corpus, client: AsyncClient, mode: SearchRetrievalMode
) -> None:
    ids = [project.id for project in corpus.projects[:2]]
    response = await client.request(
        "QUERY",
        "/v2/search/",
        json={
            "project_ids": ids,
            "text": "nebula",
            "retrieval_mode": mode.value,
            "metadata_filters": {"status": "open"},
            "note_types": ["note"],
            "file_path_prefix": "notes",
            "entity_types": ["observation"],
            "categories": ["fact"],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["results"]) == 1
    row = data["results"][0]
    assert row["project_id"] == ids[0]
    assert row["project_external_id"] == corpus.projects[0].external_id
    assert row["external_id"] and row["observation_id"] == 500
    assert data["total_is_exact"] == (mode == SearchRetrievalMode.FTS)
    assert data["total"] == int(mode == SearchRetrievalMode.FTS)
    assert not data["has_more"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_route_bypasses_cache_on_scope_change(
    corpus: Corpus, client: AsyncClient, app: FastAPI
) -> None:
    def forbidden_cache():
        pytest.fail("The database-scoped route must not acquire the project cache")

    app.dependency_overrides[get_read_cache] = forbidden_cache
    for ids, count in [([p.id for p in corpus.projects], 9), ([corpus.projects[1].id], 3), ([], 0)]:
        response = await client.request(
            "QUERY", "/v2/search/", json={"project_ids": ids, "text": "nebula"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == count
        assert {r["project_id"] for r in response.json()["results"]} <= set(ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, [0], [True], ["1"]])
async def test_route_rejects_invalid_scope(client: AsyncClient, scope: object) -> None:
    response = await client.request(
        "QUERY", "/v2/search/", json={"text": "nebula", "project_ids": scope}
    )
    assert response.status_code == 422
    response = await client.request("QUERY", "/v2/search/", json={"text": "nebula"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unsupported_adapter_is_explicit_and_fts_still_works(
    corpus: Corpus, client: AsyncClient
) -> None:
    # The capability decision follows the configured DB adapter, before embedding.
    original_backend = corpus.config.database_backend
    corpus.config.database_backend = DatabaseBackend.POSTGRES
    corpus.config.semantic_vector_index = "milvus"
    for mode in ("vector", "hybrid"):
        response = await client.request(
            "QUERY",
            "/v2/search/",
            json={"project_ids": [corpus.projects[0].id], "text": "nebula", "retrieval_mode": mode},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "unsupported_multi_project_vector_adapter"
    assert corpus.provider.query_calls == 0
    corpus.config.database_backend = original_backend
    response = await client.request(
        "QUERY",
        "/v2/search/",
        json={"project_ids": [corpus.projects[0].id], "text": "nebula", "retrieval_mode": "fts"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3


@pytest.mark.asyncio
async def test_stale_manifest_model_and_source_hash_are_excluded(corpus: Corpus) -> None:
    ids = [project.id for project in corpus.projects]
    async with db.scoped_session(corpus.session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'pending' WHERE project_id = :p"
            ),
            {"p": ids[0]},
        )
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_model = 'other-model' WHERE project_id = :p"
            ),
            {"p": ids[1]},
        )
        await session.execute(
            text("UPDATE search_vector_chunks SET source_hash = 'stale' WHERE project_id = :p"),
            {"p": ids[2]},
        )
        await session.commit()
    prepared = SearchService.prepare_query(
        SearchQuery(text="nebula", retrieval_mode=SearchRetrievalMode.VECTOR)
    )
    assert prepared is not None
    assert await corpus.repository(ids).search(prepared) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_temporal_collision_filter_and_hydration(
    corpus: Corpus, client: AsyncClient, mode: SearchRetrievalMode
) -> None:
    from basic_memory.models import MemoryTimeIndex
    from sqlalchemy import select

    ids = [project.id for project in corpus.projects[:2]]
    async with db.scoped_session(corpus.session_maker) as session:
        entities = (await session.scalars(select(Entity).where(Entity.project_id.in_(ids)))).all()
        for entity in entities:
            current = entity.project_id == ids[0]
            session.add(
                MemoryTimeIndex(
                    project_id=entity.project_id,
                    entity_id=entity.id,
                    source_type="observation",
                    source_id=500,
                    time_kind="effective",
                    range_axis="date",
                    lower_value="2026-09-01" if current else "2025-01-01",
                    upper_value=None if current else "2025-02-01",
                    lower_inclusive=True,
                    upper_inclusive=False,
                    is_empty=False,
                    extractor="test",
                    source_text="@effective[2026-09-01,)"
                    if current
                    else "@effective[2025-01-01,2025-02-01)",
                )
            )
        await session.commit()
    response = await client.request(
        "QUERY",
        "/v2/search/",
        json={
            "project_ids": ids,
            "text": "nebula",
            "retrieval_mode": mode.value,
            "valid_at": "2026-09-14",
            "note_types": ["note"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["temporal_applied"] is True
    assert len(payload["results"]) == 1
    assert payload["results"][0]["project_id"] == ids[0]
    assert payload["results"][0]["temporal"][0]["source_text"] == "@effective[2026-09-01,)"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_api_pagination_and_empty_later_page(
    corpus: Corpus, client: AsyncClient, mode: SearchRetrievalMode
) -> None:
    query = {
        "project_ids": [project.id for project in corpus.projects[:2]],
        "text": "nebula",
        "retrieval_mode": mode.value,
    }
    for page, count, has_more in [(1, 2, True), (3, 2, False), (4, 0, False)]:
        response = await client.request(
            "QUERY", "/v2/search/", params={"page": page, "page_size": 2}, json=query
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["results"]) == count
        assert response.json()["has_more"] == has_more


@pytest.mark.asyncio
async def test_repository_guards_and_disabled_route(corpus: Corpus, client: AsyncClient) -> None:
    from basic_memory.repository.postgres_search_query import PostgresSearchQuery
    from basic_memory.repository.sqlite_search_query import SQLiteSearchQuery

    for bad_scope in ([0], [-1], [True]):
        with pytest.raises(ValueError, match="positive integers"):
            corpus.repository(bad_scope)
        for compiler in (PostgresSearchQuery, SQLiteSearchQuery):
            with pytest.raises(ValueError, match="positive integers"):
                compiler(corpus.session_maker, bad_scope)
    repo = corpus.repository([corpus.projects[0].id])
    prepared = SearchService.prepare_query(
        SearchQuery(text="nebula", retrieval_mode=SearchRetrievalMode.VECTOR)
    )
    assert prepared is not None
    for limit, offset in [(0, 0), (1, -1)]:
        with pytest.raises(ValueError, match="limit"):
            await repo.search(prepared, limit=limit, offset=offset)
    no_text = SearchService.prepare_query(
        SearchQuery(title="nebula", retrieval_mode=SearchRetrievalMode.VECTOR)
    )
    assert no_text is not None
    with pytest.raises(ValueError, match="nonempty text"):
        await repo.search(no_text)
    corpus.provider.dimensions = 5
    with pytest.raises(ValueError, match="dimensions"):
        await repo.search(prepared)
    corpus.config.semantic_search_enabled = False
    response = await client.request(
        "QUERY",
        "/v2/search/",
        json={"project_ids": [corpus.projects[0].id], "text": "nebula", "retrieval_mode": "vector"},
    )
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]
    response = await client.request(
        "QUERY", "/v2/search/", json={"project_ids": [corpus.projects[0].id]}
    )
    assert response.status_code == 200 and not response.json()["results"]


@pytest.mark.asyncio
async def test_hybrid_fts_only_rows_and_threshold(corpus: Corpus) -> None:
    ids = [project.id for project in corpus.projects[:2]]
    async with db.scoped_session(corpus.session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'pending' WHERE project_id = :p"
            ),
            {"p": ids[1]},
        )
        await session.commit()
    prepared = SearchService.prepare_query(
        SearchQuery(text="nebula", retrieval_mode=SearchRetrievalMode.HYBRID, min_similarity=1)
    )
    assert prepared is not None
    rows = await corpus.repository(ids).search(prepared, limit=100)
    assert len(rows) == 6
    assert all(row.matched_chunk_text is None for row in rows if row.project_id == ids[1])
    assert max(row.score or 0.0 for row in rows) == pytest.approx(1.3)
    assert all(1.0 <= (row.score or 0.0) <= 1.3 + 1e-6 for row in rows if row.project_id == ids[0])
    assert all(0.0 <= (row.score or 0.0) <= 1.0 for row in rows if row.project_id == ids[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
async def test_compare_project_pipelines_on_same_corpus(
    corpus: Corpus, mode: SearchRetrievalMode
) -> None:
    """Record bounded corpus equivalence and actual cold/warm retrieval costs."""
    import json
    import time
    from dataclasses import asdict

    repo_type = (
        PostgresSearchRepository
        if corpus.config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    writers = [
        repo_type(
            corpus.session_maker,
            project.id,
            app_config=corpus.config,
            embedding_provider=corpus.provider,
        )
        for project in corpus.projects
    ]
    reader = corpus.repository([project.id for project in corpus.projects])
    prepared = SearchService.prepare_query(SearchQuery(text="nebula", retrieval_mode=mode))
    assert prepared is not None
    for temperature in ("cold", "warm"):
        corpus.provider.query_calls = 0
        started = time.perf_counter()
        baseline = [
            row
            for writer in writers
            for row in await writer.search(search_text="nebula", retrieval_mode=mode, limit=100)
        ]
        baseline_ms = (time.perf_counter() - started) * 1000
        assert corpus.provider.query_calls == len(writers)
        corpus.provider.query_calls = 0
        started = time.perf_counter()
        combined = await reader.search(prepared, limit=100)
        combined_ms = (time.perf_counter() - started) * 1000
        assert corpus.provider.query_calls == 1
        assert {(r.project_id, r.type, r.id) for r in combined} == {
            (r.project_id, r.type, r.id) for r in baseline
        }
        print(
            json.dumps(
                {
                    "backend": corpus.config.database_backend.value,
                    "mode": mode.value,
                    "temperature": temperature,
                    "projects": len(writers),
                    "results": len(combined),
                    "baseline_embeddings": len(writers),
                    "combined_embeddings": 1,
                    "baseline_ms": round(baseline_ms, 2),
                    "combined_ms": round(combined_ms, 2),
                    "baseline_payload_bytes": len(
                        json.dumps([asdict(row) for row in baseline], default=str)
                    ),
                    "combined_payload_bytes": len(
                        json.dumps([asdict(row) for row in combined], default=str)
                    ),
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fts", "hybrid"])
@pytest.mark.parametrize("query", ["Did nebula go hiking at sunrise?", "foo<nebula baz qux"])
async def test_relaxed_route_preserves_scope_counts_and_pages(
    corpus: Corpus, client: AsyncClient, mode: str, query: str
) -> None:
    ids = [project.id for project in corpus.projects[:2]]
    # Remove the vector channel so a hybrid success proves lexical recovery.
    async with db.scoped_session(corpus.session_maker) as session:
        await session.execute(text("UPDATE search_vector_chunks SET embedding_status = 'pending'"))
        await session.commit()
    body = {"project_ids": ids, "text": query, "retrieval_mode": mode}
    complete = await client.request("QUERY", "/v2/search/", json=body)
    assert complete.status_code == 200, complete.text
    expected = complete.json()["results"]
    assert len(expected) == 6
    assert {row["project_id"] for row in expected} == set(ids)
    if mode == "fts":
        assert complete.json()["total"] == 6
    pages = []
    for page in range(1, 5):
        response = await client.request(
            "QUERY", "/v2/search/", json=body, params={"page": page, "page_size": 2}
        )
        assert response.status_code == 200, response.text
        pages.extend(response.json()["results"])
        assert response.json()["has_more"] == (page < 3)
    assert pages == expected
    assert corpus.provider.query_calls == (5 if mode == "hybrid" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fts", "hybrid"])
async def test_relaxation_keeps_strict_matches_on_deep_pages(
    corpus: Corpus, client: AsyncClient, mode: str
) -> None:
    # Three terms permit relaxation, but strict matches anywhere in the selected
    # scope must suppress it even after the final page.
    body = {
        "project_ids": [project.id for project in corpus.projects[:2]],
        "text": "shared nebula observation",
        "retrieval_mode": mode,
        "min_similarity": 1,
    }
    async with db.scoped_session(corpus.session_maker) as session:
        await session.execute(text("UPDATE search_vector_chunks SET embedding_status = 'pending'"))
        await session.commit()
    first = await client.request("QUERY", "/v2/search/", json=body)
    assert first.status_code == 200, first.text
    assert len(first.json()["results"]) == 2
    later = await client.request("QUERY", "/v2/search/", json=body, params={"page": 2})
    assert later.status_code == 200, later.text
    assert later.json()["results"] == []
    if mode == "fts":
        assert later.json()["total"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["package", "extension"])
async def test_sqlite_semantic_dependency_errors_are_actionable(
    corpus: Corpus, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if corpus.config.database_backend == DatabaseBackend.POSTGRES:
        pytest.skip("SQLite connection capability boundary")
    import sys
    import aiosqlite

    if failure == "package":
        monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    else:

        async def unavailable(_self, _enabled):
            raise AttributeError("enable_load_extension")

        monkeypatch.setattr(aiosqlite.Connection, "enable_load_extension", unavailable)
    for mode in ("vector", "hybrid"):
        response = await client.request(
            "QUERY",
            "/v2/search/",
            json={"project_ids": [corpus.projects[0].id], "text": "nebula", "retrieval_mode": mode},
        )
        assert response.status_code == 400, response.text
        assert ("sqlite-vec" if failure == "package" else "extension loading") in response.json()[
            "detail"
        ]
    response = await client.request(
        "QUERY", "/v2/search/", json={"project_ids": [corpus.projects[0].id], "text": "nebula"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["foo<bar", "nebula AND unavailable"])
@pytest.mark.parametrize("mode", ["fts", "hybrid"])
async def test_invalid_or_explicit_fts_does_not_broaden_lexical_channel(
    corpus: Corpus, client: AsyncClient, query: str, mode: str
) -> None:
    response = await client.request(
        "QUERY",
        "/v2/search/",
        json={"project_ids": [corpus.projects[0].id], "text": query, "retrieval_mode": mode},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["results"]) == (3 if mode == "hybrid" else 0)
    if mode == "fts":
        assert response.json()["total"] == 0
    assert corpus.provider.query_calls == int(mode == "hybrid")
