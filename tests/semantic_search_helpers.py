"""Deterministic semantic providers and corpus fixtures shared by unit/integration suites."""

from datetime import datetime, timezone
from typing import Any

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.models import Entity
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType


type BackendSearchRepository = SQLiteSearchRepository | PostgresSearchRepository


class _StubEmbeddingProvider:
    """Deterministic embeddings that give the two auth notes DIFFERENT similarity.

    An "auth" doc containing "deep" is tilted slightly off the query axis, so vector
    retrieval ranks the plain-auth note strictly above it. That makes the pre-rerank
    baseline a real ordering (not a tie), so a rerank that promotes the lower note is
    a genuine "recover the below-cutoff doc" scenario (#950), not a coin flip.
    """

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(t) for t in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        lowered = text.lower()
        if "auth" not in lowered:
            return [0.0, 0.0, 0.0, 1.0]
        # Unit vectors; cos with the query axis [1,0,0,0] is 1.0 vs 0.9.
        if "deep" in lowered:
            return [0.9, 0.4358898943540674, 0.0, 0.0]
        return [1.0, 0.0, 0.0, 0.0]


class _FakeReranker:
    """Scores a document by the marker substring it contains; records call count."""

    model_name = "fake-reranker"

    def __init__(self, score_by_marker: dict[str, float]):
        self.score_by_marker = score_by_marker
        self.calls = 0
        self.document_batches: list[list[str]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        self.document_batches.append(documents)
        scores = []
        for doc in documents:
            score = 0.0
            for marker, value in self.score_by_marker.items():
                if marker in doc:
                    score = value
            scores.append(score)
        return scores

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


def _entity_row(*, project_id: int, row_id: int, title: str, permalink: str, content: str):
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        type=SearchItemType.ENTITY.value,
        title=title,
        permalink=permalink,
        file_path=f"{permalink}.md",
        metadata={"note_type": "spec"},
        entity_id=row_id,
        content_stems=content,
        content_snippet=content,
        created_at=now,
        updated_at=now,
    )


def _semantic_search_repository(
    session_maker: Any,
    project_id: int,
    app_config: BasicMemoryConfig,
    **config_updates: object,
) -> BackendSearchRepository:
    config = app_config.model_copy(
        update={
            "semantic_search_enabled": True,
            "semantic_min_similarity": 0.0,
            **config_updates,
        }
    )
    repository_type = (
        PostgresSearchRepository
        if config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    return repository_type(
        session_maker,
        project_id=project_id,
        app_config=config,
        embedding_provider=_StubEmbeddingProvider(),
    )


@pytest.fixture
def rerank_search_repository(
    session_maker: Any,
    test_project: Any,
    app_config: BasicMemoryConfig,
) -> BackendSearchRepository:
    return _semantic_search_repository(session_maker, test_project.id, app_config)


@pytest.fixture
async def pagination_repository(
    rerank_search_repository: BackendSearchRepository,
) -> BackendSearchRepository:
    repo = rerank_search_repository
    repo._semantic_vector_k = 8
    repo._reranker_candidates = 2
    repo._reranker_max_document_chars = 2000
    await repo.init_search_index()
    for index in range(32):
        # Distinct chunk passages straddle the fixed prefix. Some rows have only
        # lexical evidence, others only vector evidence, and one is filter-rejected.
        content = "auth session token " + ("deep " if index % 2 else "")
        if index < 2:
            content = "\n\n".join(f"{content}passage {n} " + "detail " * 160 for n in range(3))
        if index == 30:
            content = "oauth related concepts without lexical terms"
        row = _entity_row(
            project_id=repo.project_id,
            row_id=700 + index,
            title=f"Note {index:02d}",
            permalink=f"{'excluded' if index == 31 else 'notes'}/{index:02d}",
            content=content,
        )
        async with db.scoped_session(repo.session_maker) as session:
            session.add(
                Entity(
                    id=row.id,
                    project_id=repo.project_id,
                    title=row.title,
                    note_type="spec",
                    content_type="text/markdown",
                    permalink=row.permalink,
                    file_path=row.file_path,
                    entity_metadata={"status": "active"},
                )
            )
            await session.commit()
        await repo.index_item(row)
        if index != 29:
            await repo.sync_entity_vectors(row.id)
    return repo
