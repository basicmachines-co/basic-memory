"""One query over an explicit set of projects, through the reader and the route.

Only the embedding provider is a double; projects, entities, search rows, vectors,
retrieval, and hydration are real on whichever backend the session is configured
for. Search row ids are database-wide primary keys, so the corpus gives every row a
distinct id the way real data does.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import basic_memory.repository.search_repository as search_repository_module
from basic_memory import db
from basic_memory.api.v2.utils import to_search_results
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.models import Entity, MemoryTimeIndex, Project
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository import create_search_reader
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode
from basic_memory.services.scoped_search_service import ScopedSearchService
from basic_memory.services.search_service import (
    include_legacy_note_type_spellings,
    prepare_search_query,
)

MARKERS = ("nebula", "comet", "quasar")
# Distinct per project, as real observation and relation primary keys are.
OBSERVATION_IDS = (5001, 5002, 5003)
RELATION_IDS = (6001, 6002, 6003)
SEMANTIC_MODES = [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID]


class MarkerEmbeddingProvider:
    """Unit vectors from marker words: deterministic, and different texts rank differently."""

    model_name = "marker"
    dimensions = 4

    def __init__(self) -> None:
        self.query_calls = 0

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        words = set(re.findall(r"[a-z]+", text.lower()))
        axes = [1.0 if marker in words else 0.0 for marker in MARKERS]
        axes.append(0.0 if any(axes) else 1.0)
        norm = sqrt(sum(axis * axis for axis in axes))
        return [axis / norm for axis in axes]

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(text) for text in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


@dataclass
class Corpus:
    session_maker: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    config: BasicMemoryConfig
    projects: list[Project]
    entities: list[Entity]
    provider: MarkerEmbeddingProvider

    def ids(self, count: int) -> list[int]:
        return [project.id for project in self.projects[:count]]

    def service(self, project_ids: list[int]) -> ScopedSearchService:
        scope = ProjectScope.of(project_ids)
        reader = create_search_reader(self.session_maker, scope, self.config)
        return ScopedSearchService(self.session_maker, scope, reader)


@pytest.fixture
async def corpus(
    engine_factory, app_config: BasicMemoryConfig, monkeypatch: pytest.MonkeyPatch
) -> Corpus:
    """Three projects with one note each, all about a nebula, embedded for real.

    Project 0 matches the query vector exactly; projects 1 and 2 carry a second
    marker so their vectors sit at cosine 0.707. Project 2 spells its note type the
    legacy way, so note-type expansion has something to find.
    """
    engine, session_maker = engine_factory
    app_config.semantic_search_enabled = True
    app_config.reranker_enabled = False
    app_config.semantic_min_similarity = 0.0
    provider = MarkerEmbeddingProvider()
    monkeypatch.setattr(
        search_repository_module, "create_embedding_provider", lambda _config: provider
    )
    repository_type = (
        PostgresSearchRepository
        if app_config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    projects: list[Project] = []
    entities: list[Entity] = []
    for index, flavor in enumerate(("", " comet", " quasar")):
        note_type = "Note" if index == 2 else "note"
        async with db.scoped_session(session_maker) as session:
            project = Project(
                name=f"scope-{index}", permalink=f"scope-{index}", path=f"/scope-{index}"
            )
            session.add(project)
            await session.flush()
            entity = Entity(
                project_id=project.id,
                title="shared nebula",
                note_type=note_type,
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
        entities.append(entity)

        writer = repository_type(
            session_maker, project.id, app_config=app_config, embedding_provider=provider
        )
        await writer.init_search_index()
        content = f"shared nebula{flavor}"
        rows = [
            SearchIndexRow(
                project_id=project.id,
                id=entity.id,
                entity_id=entity.id,
                type="entity",
                file_path=entity.file_path,
                permalink=entity.permalink,
                title=entity.title,
                content_stems=content,
                content_snippet=content,
                metadata={"note_type": note_type},
                created_at=now,
                updated_at=now,
            )
        ]
        for row_type, row_id in (
            ("observation", OBSERVATION_IDS[index]),
            ("relation", RELATION_IDS[index]),
        ):
            rows.append(
                SearchIndexRow(
                    project_id=project.id,
                    id=row_id,
                    entity_id=entity.id,
                    type=row_type,
                    file_path=entity.file_path,
                    permalink=f"notes/shared/{row_type}",
                    title="shared nebula",
                    content_stems=content,
                    content_snippet=f"{content} project {index} {row_type}",
                    category="fact" if row_type == "observation" else None,
                    from_id=entity.id if row_type == "relation" else None,
                    relation_type="relates_to" if row_type == "relation" else None,
                    created_at=now,
                    updated_at=now,
                )
            )
        await writer.bulk_index_items(rows)
        await writer.sync_entity_vectors(entity.id)
    return Corpus(session_maker, engine, app_config, projects, entities, provider)


async def _mark_pending(corpus: Corpus, project_ids: list[int] | None = None) -> None:
    """Take vectors out of play so a hybrid answer proves the lexical channel."""
    async with db.scoped_session(corpus.session_maker) as session:
        if project_ids is None:
            await session.execute(
                text("UPDATE search_vector_chunks SET embedding_status = 'pending'")
            )
        else:
            for project_id in project_ids:
                await session.execute(
                    text(
                        "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                        "WHERE project_id = :project_id"
                    ),
                    {"project_id": project_id},
                )
        await session.commit()


async def _add_temporal_assertions(corpus: Corpus, project_ids: list[int]) -> None:
    """Project 0's observation is current; every other project's ended in 2025."""
    async with db.scoped_session(corpus.session_maker) as session:
        for index, (project, entity) in enumerate(zip(corpus.projects, corpus.entities)):
            if project.id not in project_ids:
                continue
            current = index == 0
            session.add(
                MemoryTimeIndex(
                    project_id=project.id,
                    entity_id=entity.id,
                    source_type="observation",
                    source_id=OBSERVATION_IDS[index],
                    time_kind="effective",
                    range_axis="date",
                    lower_value="2026-09-01" if current else "2025-01-01",
                    upper_value=None if current else "2025-02-01",
                    lower_inclusive=True,
                    upper_inclusive=False,
                    is_empty=False,
                    extractor="test",
                    source_text=(
                        "@effective[2026-09-01,)"
                        if current
                        else "@effective[2025-01-01,2025-02-01)"
                    ),
                )
            )
        await session.commit()


# --- The reader over a scope ---


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
@pytest.mark.parametrize("scope_size", [0, 1, 2])
async def test_one_reader_answers_the_whole_scope(
    corpus: Corpus, mode: SearchRetrievalMode, scope_size: int
) -> None:
    """Every project in scope answers from one pipeline, and the pipeline only reads."""
    service = corpus.service(corpus.ids(scope_size))
    query = SearchQuery(text="nebula", retrieval_mode=mode)
    statements: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(corpus.engine.sync_engine, "before_cursor_execute", record)
    try:
        rows = await service.search(query, limit=100, offset=0)
    finally:
        event.remove(corpus.engine.sync_engine, "before_cursor_execute", record)

    assert len(rows) == scope_size * 3
    assert {row.project_id for row in rows} == set(corpus.ids(scope_size))
    assert len({(row.type, row.id) for row in rows}) == len(rows)
    # One embedding per search, and none at all for an empty scope.
    assert corpus.provider.query_calls == int(scope_size > 0 and mode != SearchRetrievalMode.FTS)
    mutations = [
        sql
        for sql in statements
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "DROP"))
    ]
    assert mutations == []
    if mode == SearchRetrievalMode.FTS:
        assert await service.count(query) == scope_size * 3
    else:
        assert all(row.matched_chunk_text for row in rows)
        with pytest.raises(ValueError, match="Exact counts"):
            await service.count(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_pages_are_a_stable_slice_of_the_complete_order(
    corpus: Corpus, mode: SearchRetrievalMode
) -> None:
    service = corpus.service(corpus.ids(2))
    query = SearchQuery(text="nebula", retrieval_mode=mode)

    complete = await service.search(query, limit=100, offset=0)
    pages = [
        row
        for offset in range(0, len(complete), 2)
        for row in await service.search(query, limit=2, offset=offset)
    ]

    assert [(r.project_id, r.type, r.id, r.score) for r in pages] == [
        (r.project_id, r.type, r.id, r.score) for r in complete
    ]
    assert await service.search(query, limit=10, offset=100) == []


@pytest.mark.asyncio
async def test_vectors_outside_the_current_manifest_are_not_served(corpus: Corpus) -> None:
    """Pending, re-modelled, and stale-source chunks are invisible across the whole scope."""
    ids = corpus.ids(3)
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

    query = SearchQuery(text="nebula", retrieval_mode=SearchRetrievalMode.VECTOR)
    assert await corpus.service(ids).search(query, limit=100, offset=0) == []


@pytest.mark.asyncio
async def test_hybrid_keeps_lexical_only_rows_below_the_threshold(corpus: Corpus) -> None:
    ids = corpus.ids(2)
    await _mark_pending(corpus, [ids[1]])
    query = SearchQuery(text="nebula", retrieval_mode=SearchRetrievalMode.HYBRID, min_similarity=1)

    rows = await corpus.service(ids).search(query, limit=100, offset=0)

    assert len(rows) == 6
    assert all(row.matched_chunk_text is None for row in rows if row.project_id == ids[1])
    assert max(row.score or 0.0 for row in rows) == pytest.approx(1.3)
    assert all(1.0 <= (row.score or 0.0) <= 1.3 + 1e-6 for row in rows if row.project_id == ids[0])
    assert all(0.0 <= (row.score or 0.0) <= 1.0 for row in rows if row.project_id == ids[1])


@pytest.mark.asyncio
async def test_legacy_note_type_spellings_expand_only_within_scope(corpus: Corpus) -> None:
    """A scope learns the spellings its own projects store, and nothing about others."""
    prepared = prepare_search_query(SearchQuery(text="nebula", note_types=["note"]))
    assert prepared is not None
    canonical_only = ProjectScope.of(corpus.ids(2))
    legacy_project = ProjectScope.single(corpus.projects[2].id)

    within_canonical = await include_legacy_note_type_spellings(
        corpus.session_maker, canonical_only, prepared
    )
    within_legacy = await include_legacy_note_type_spellings(
        corpus.session_maker, legacy_project, prepared
    )

    assert within_canonical.note_types == ["note"]
    assert within_legacy.note_types == ["Note", "note"]
    # And the expanded filter finds the legacy rows when that project is in scope.
    query = SearchQuery(text="nebula", note_types=["note"])
    assert len(await corpus.service(corpus.ids(3)).search(query, limit=100, offset=0)) == 9
    assert (
        len(await corpus.service([corpus.projects[2].id]).search(query, limit=100, offset=0)) == 3
    )


@pytest.mark.asyncio
async def test_hydration_reads_only_projects_in_scope(corpus: Corpus) -> None:
    ids = corpus.ids(2)
    await _add_temporal_assertions(corpus, ids)
    service = corpus.service([ids[0]])
    all_entity_ids = [entity.id for entity in corpus.entities]

    entities = await service.get_entities_by_id(all_entity_ids)
    # Search before opening the hydration session: the test pool holds one connection.
    rows = await service.search(SearchQuery(text="nebula"), limit=100, offset=0)
    async with db.scoped_session(corpus.session_maker) as session:
        assertions = await service.find_for_sources(
            session, [("observation", source_id) for source_id in OBSERVATION_IDS]
        )
        external_ids = await service.project_external_ids(session, rows)

    assert [entity.id for entity in entities] == [corpus.entities[0].id]
    assert [row.project_id for row in assertions] == [ids[0]]
    assert external_ids == {ids[0]: corpus.projects[0].external_id}
    # An empty scope hydrates nothing, and an empty page names no projects.
    unscoped = corpus.service([])
    assert await unscoped.get_entities_by_id(all_entity_ids) == []
    async with db.scoped_session(corpus.session_maker) as session:
        assert await unscoped.find_for_sources(session, [("observation", OBSERVATION_IDS[0])]) == []
        assert await service.project_external_ids(session, []) == {}


# --- The route ---


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_filters_narrow_the_scope_and_results_carry_project_identity(
    corpus: Corpus, client: AsyncClient, mode: SearchRetrievalMode
) -> None:
    ids = corpus.ids(2)

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
    assert row["external_id"] == corpus.entities[0].external_id
    assert row["observation_id"] == OBSERVATION_IDS[0]
    assert row["entity"] == "notes/shared"
    assert data["total_is_exact"] == (mode == SearchRetrievalMode.FTS)
    assert data["total"] == int(mode == SearchRetrievalMode.FTS)
    assert not data["has_more"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["accept-query"] == "application/json"


@pytest.mark.asyncio
async def test_scope_shapes_the_answer(corpus: Corpus, client: AsyncClient) -> None:
    for ids, count in [(corpus.ids(3), 9), ([corpus.projects[1].id], 3), ([], 0)]:
        response = await client.request(
            "QUERY", "/v2/search/", json={"project_ids": ids, "text": "nebula"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == count
        assert {r["project_id"] for r in response.json()["results"]} <= set(ids)


@pytest.mark.asyncio
async def test_post_is_the_documented_twin_of_query(
    corpus: Corpus, client: AsyncClient, app
) -> None:
    body = {"project_ids": corpus.ids(2), "text": "nebula"}

    posted = await client.post("/v2/search/", json=body)
    queried = await client.request("QUERY", "/v2/search/", json=body)

    assert posted.status_code == 200, posted.text
    assert posted.json() == queried.json()
    assert set(app.openapi()["paths"]["/v2/search/"]) == {"post"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, [0], [True], ["1"]])
async def test_route_rejects_an_unspecified_or_invalid_scope(
    client: AsyncClient, scope: object
) -> None:
    response = await client.request(
        "QUERY", "/v2/search/", json={"text": "nebula", "project_ids": scope}
    )
    assert response.status_code == 422
    response = await client.request("QUERY", "/v2/search/", json={"text": "nebula"})
    assert response.status_code == 422
    response = await client.request(
        "QUERY", "/v2/search/", params={"page": 0}, json={"text": "nebula", "project_ids": [1]}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SearchRetrievalMode))
async def test_temporal_filter_narrows_and_explains_within_scope(
    corpus: Corpus, client: AsyncClient, mode: SearchRetrievalMode
) -> None:
    ids = corpus.ids(2)
    await _add_temporal_assertions(corpus, ids)

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
    query = {"project_ids": corpus.ids(2), "text": "nebula", "retrieval_mode": mode.value}
    for page, count, has_more in [(1, 2, True), (3, 2, False), (4, 0, False)]:
        response = await client.request(
            "QUERY", "/v2/search/", params={"page": page, "page_size": 2}, json=query
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["results"]) == count
        assert response.json()["has_more"] == has_more


@pytest.mark.asyncio
async def test_semantic_modes_need_the_semantic_stack(corpus: Corpus, client: AsyncClient) -> None:
    corpus.config.semantic_search_enabled = False
    scope = {"project_ids": [corpus.projects[0].id]}

    for mode in SEMANTIC_MODES:
        response = await client.request(
            "QUERY", "/v2/search/", json={**scope, "text": "nebula", "retrieval_mode": mode.value}
        )
        assert response.status_code == 400, response.text
        assert "disabled" in response.json()["detail"]
    assert corpus.provider.query_calls == 0

    no_criteria = await client.request("QUERY", "/v2/search/", json=scope)
    assert no_criteria.status_code == 200 and not no_criteria.json()["results"]
    lexical = await client.request("QUERY", "/v2/search/", json={**scope, "text": "nebula"})
    assert lexical.status_code == 200, lexical.text
    assert lexical.json()["total"] == 3


@pytest.mark.asyncio
async def test_project_route_results_carry_project_identity(
    corpus: Corpus, client: AsyncClient
) -> None:
    project = corpus.projects[0]

    response = await client.post(
        f"/v2/projects/{project.external_id}/search/", json={"text": "nebula"}
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert len(results) == 3
    assert {row["project_id"] for row in results} == {project.id}
    assert {row["project_external_id"] for row in results} == {project.external_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fts", "hybrid"])
@pytest.mark.parametrize("query", ["Did nebula go hiking at sunrise?", "foo<nebula baz qux"])
async def test_relaxed_lexical_queries_keep_scope_counts_and_pages(
    corpus: Corpus, client: AsyncClient, mode: str, query: str
) -> None:
    ids = corpus.ids(2)
    # Remove the vector channel so a hybrid success proves lexical recovery.
    await _mark_pending(corpus)
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
    await _mark_pending(corpus)
    body = {
        "project_ids": corpus.ids(2),
        "text": "shared nebula observation",
        "retrieval_mode": mode,
        "min_similarity": 1,
    }

    first = await client.request("QUERY", "/v2/search/", json=body)
    later = await client.request("QUERY", "/v2/search/", json=body, params={"page": 2})

    assert first.status_code == 200, first.text
    assert len(first.json()["results"]) == 2
    assert later.status_code == 200, later.text
    assert later.json()["results"] == []
    if mode == "fts":
        assert later.json()["total"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["foo<bar", "nebula AND unavailable"])
@pytest.mark.parametrize("mode", ["fts", "hybrid"])
async def test_invalid_or_explicit_boolean_fts_does_not_broaden_the_lexical_channel(
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


# --- Hydration shaping ---


@pytest.mark.asyncio
async def test_results_name_their_project_when_the_caller_knows_it() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    row = SearchIndexRow(
        project_id=7,
        id=1,
        type="entity",
        title="Alpha",
        file_path="alpha.md",
        created_at=now,
        updated_at=now,
    )
    lookup = AsyncMock()
    lookup.get_entities_by_id = AsyncMock(return_value=[])

    named = await to_search_results(lookup, [row], project_external_ids={7: "project-7"})
    unnamed = await to_search_results(lookup, [row])

    assert (named[0].project_id, named[0].project_external_id) == (7, "project-7")
    assert (unnamed[0].project_id, unnamed[0].project_external_id) == (7, None)
