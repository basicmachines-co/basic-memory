"""Service-level integration test for the entity-aware ranking boost (#951).

Drives a fully wired SearchService over a real database with a deterministic stub
embedding provider so vector similarity is controlled. Verifies that when the boost
is enabled, an entity-matching document outranks a higher-similarity non-matching
document, and that ordering is unchanged when the boost is disabled.

No model inference is involved: the stub provider returns fixed unit vectors, so the
test is fast and deterministic on both SQLite and Postgres.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
import pytest_asyncio

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode
from basic_memory.services.entity_service import EntityService
from basic_memory.services.file_service import FileService
from basic_memory.services.search_service import SearchService


# --- Deterministic stub embedding provider ---

_STUB_DIMENSIONS = 4


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector)) or 1.0
    return [component / norm for component in vector]


class _StubEmbeddingProvider:
    """Maps known text fragments to fixed unit vectors for controlled similarity.

    The query is engineered to sit closer (cosine) to the non-matching "hobbies"
    document than to the gold "Joanna" document, reproducing the #951 failure where
    generic semantic similarity outranks the entity-matching gold doc.
    """

    model_name = "stub-entity-boost"
    dimensions = _STUB_DIMENSIONS

    def _vector_for(self, text: str) -> list[float]:
        lowered = text.lower()
        if "joanna" in lowered:
            # Gold doc: shares some direction with the query but less than the decoy.
            return _unit([0.6, 0.8, 0.0, 0.0])
        if "hobbies" in lowered or "pastime" in lowered:
            # Decoy doc: closest to the query direction.
            return _unit([0.95, 0.31, 0.0, 0.0])
        return _unit([0.0, 0.0, 1.0, 0.0])

    async def embed_query(self, text: str) -> list[float]:
        # Query direction is closest to the decoy vector above.
        return _unit([0.97, 0.24, 0.0, 0.0])

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector_for(text) for text in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


# --- Fixtures ---


async def _build_search_service(
    *,
    session_maker,
    test_project,
    base_app_config: BasicMemoryConfig,
    file_service: FileService,
    entity_repository: EntityRepository,
    boost_enabled: bool,
) -> SearchService:
    """Build a SearchService with semantic search + a deterministic stub provider."""
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
    from basic_memory.config import DatabaseBackend

    app_config = base_app_config.model_copy(
        update={
            "semantic_search_enabled": True,
            "semantic_min_similarity": 0.0,
            "search_entity_boost_enabled": boost_enabled,
            "search_entity_boost_weight": 0.3,
            "search_entity_boost_max_terms": 3,
        }
    )

    provider = _StubEmbeddingProvider()
    if app_config.database_backend == DatabaseBackend.POSTGRES:  # pragma: no cover
        search_repo: SearchRepository = PostgresSearchRepository(
            session_maker,
            project_id=test_project.id,
            app_config=app_config,
            embedding_provider=provider,
        )
    else:
        # Pass the stub provider at construction time so __init__ does not
        # instantiate the real configured provider when semantic_search_enabled=True.
        repo = SQLiteSearchRepository(
            session_maker,
            project_id=test_project.id,
            app_config=app_config,
            embedding_provider=provider,
        )
        search_repo = repo

    service = SearchService(search_repo, entity_repository, file_service)
    await service.init_search_index()
    return service


@pytest_asyncio.fixture
async def boost_entities(
    entity_service: EntityService,
):
    """Index two entities: a decoy 'hobbies' doc and the gold 'Joanna' doc."""
    decoy, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Common Hobbies and Pastimes",
            note_type="note",
            directory="people",
            content="A general overview of hobbies and pastimes people enjoy.",
        )
    )
    gold, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Joanna",
            note_type="note",
            directory="people",
            content="Notes about Joanna and what she likes to do.",
        )
    )
    return decoy, gold


# --- Tests ---


async def _sync_vectors(service: SearchService, entity_ids: list[int]) -> None:
    """Embed the indexed entities via the stub provider."""
    await service.sync_entity_vectors_batch(entity_ids)


@pytest.mark.asyncio
async def test_entity_boost_enabled_promotes_gold_doc(
    session_maker,
    test_project,
    app_config,
    file_service,
    entity_repository,
    boost_entities,
):
    decoy, gold = boost_entities
    service = await _build_search_service(
        session_maker=session_maker,
        test_project=test_project,
        base_app_config=app_config,
        file_service=file_service,
        entity_repository=entity_repository,
        boost_enabled=True,
    )
    # Re-index the entities through this service so vector tables exist for it.
    for entity in (decoy, gold):
        await service.index_entity(entity)
    await _sync_vectors(service, [decoy.id, gold.id])

    results = await service.search(
        SearchQuery(
            text="What are Joanna's hobbies?",
            retrieval_mode=SearchRetrievalMode.HYBRID,
        ),
        limit=10,
    )

    entity_ids = [r.entity_id for r in results]
    assert gold.id in entity_ids and decoy.id in entity_ids
    # With the boost on, the entity-matching gold doc ranks ahead of the
    # higher-similarity decoy.
    assert entity_ids.index(gold.id) < entity_ids.index(decoy.id)


@pytest.mark.asyncio
async def test_entity_boost_disabled_keeps_similarity_order(
    session_maker,
    test_project,
    app_config,
    file_service,
    entity_repository,
    boost_entities,
):
    decoy, gold = boost_entities
    service = await _build_search_service(
        session_maker=session_maker,
        test_project=test_project,
        base_app_config=app_config,
        file_service=file_service,
        entity_repository=entity_repository,
        boost_enabled=False,
    )
    for entity in (decoy, gold):
        await service.index_entity(entity)
    await _sync_vectors(service, [decoy.id, gold.id])

    results = await service.search(
        SearchQuery(
            text="What are Joanna's hobbies?",
            retrieval_mode=SearchRetrievalMode.HYBRID,
        ),
        limit=10,
    )

    entity_ids = [r.entity_id for r in results]
    assert gold.id in entity_ids and decoy.id in entity_ids
    # With the boost off, the higher-similarity decoy ranks ahead of the gold doc.
    assert entity_ids.index(decoy.id) < entity_ids.index(gold.id)
