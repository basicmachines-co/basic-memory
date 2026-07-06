"""Milvus-backed semantic vector repository tests."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import (
    BasicMemoryConfig,
    DatabaseBackend,
    ProjectEntry,
    SemanticVectorBackend,
)
from basic_memory.repository.milvus_search_repository import (
    SQLiteMilvusSearchRepository,
)
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class StubEmbeddingProvider:
    """Deterministic embedding provider for Milvus repository tests."""

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(text) for text in texts]

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        normalized = text.lower()
        if any(token in normalized for token in ["auth", "token", "session", "login"]):
            return [1.0, 0.0, 0.0, 0.0]
        if any(token in normalized for token in ["schema", "migration", "database", "sql"]):
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


class FakeDataType:
    """Small stand-in for pymilvus.DataType."""

    VARCHAR = "VARCHAR"
    INT64 = "INT64"
    FLOAT_VECTOR = "FLOAT_VECTOR"


class FakeSchema:
    """Collect schema fields added by the repository."""

    def __init__(self) -> None:
        self.fields: list[dict[str, Any]] = []

    def add_field(self, **kwargs: Any) -> None:
        params: dict[str, Any] = {}
        if "dim" in kwargs:
            params["dim"] = kwargs["dim"]
        if "max_length" in kwargs:
            params["max_length"] = kwargs["max_length"]
        field = {
            "name": kwargs["field_name"],
            "type": kwargs["datatype"],
            "params": params,
        }
        if kwargs.get("is_primary"):
            field["is_primary"] = True
        self.fields.append(field)


class FakeIndexParams:
    """Collect index definitions added by the repository."""

    def __init__(self) -> None:
        self.indexes: list[dict[str, Any]] = []

    def add_index(self, **kwargs: Any) -> None:
        self.indexes.append(dict(kwargs))


class FakeMilvusClient:
    """In-memory MilvusClient double with collection-level state."""

    collections: dict[str, dict[str, Any]] = {}
    instances: list["FakeMilvusClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.collections = {}
        cls.instances = []

    def has_collection(self, *, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_schema(self, *, auto_id: bool, enable_dynamic_field: bool) -> FakeSchema:
        assert auto_id is False
        assert enable_dynamic_field is False
        return FakeSchema()

    def prepare_index_params(self) -> FakeIndexParams:
        return FakeIndexParams()

    def create_collection(
        self,
        *,
        collection_name: str,
        schema: FakeSchema,
        index_params: FakeIndexParams,
    ) -> None:
        self.collections[collection_name] = {
            "fields": list(schema.fields),
            "indexes": list(index_params.indexes),
            "records": {},
        }

    def describe_collection(self, *, collection_name: str) -> dict[str, Any]:
        collection = self.collections[collection_name]
        return {"fields": collection["fields"]}

    def upsert(self, *, collection_name: str, data: list[dict[str, Any]]) -> None:
        records = self.collections[collection_name]["records"]
        for record in data:
            records[record["id"]] = dict(record)

    def search(
        self,
        *,
        collection_name: str,
        data: list[list[float]],
        anns_field: str,
        limit: int,
        filter: str,
        output_fields: list[str],
    ) -> list[list[dict[str, Any]]]:
        assert anns_field == "embedding"
        assert output_fields == ["chunk_id", "entity_id", "chunk_key", "chunk_text"]
        project_id = int(filter.split("==", 1)[1].strip())
        query = data[0]
        hits = []
        for record in self.collections[collection_name]["records"].values():
            if int(record["project_id"]) != project_id:
                continue
            score = sum(a * b for a, b in zip(query, record["embedding"], strict=True))
            norm = math.sqrt(sum(value * value for value in record["embedding"])) or 1.0
            hits.append(
                {
                    "id": record["id"],
                    "distance": score / norm,
                    "entity": {field: record[field] for field in output_fields},
                }
            )
        hits.sort(key=lambda hit: hit["distance"], reverse=True)
        return [hits[:limit]]

    def delete(
        self,
        *,
        collection_name: str,
        ids: list[str] | None = None,
        filter: str | None = None,
    ) -> None:
        records = self.collections[collection_name]["records"]
        if ids is not None:
            for record_id in ids:
                records.pop(record_id, None)
            return

        assert filter is not None
        filters = {
            part.strip().split("==", 1)[0].strip(): int(part.strip().split("==", 1)[1])
            for part in filter.split("and")
        }
        for record_id, record in list(records.items()):
            if all(int(record[field]) == expected for field, expected in filters.items()):
                records.pop(record_id, None)


def _milvus_config(tmp_path) -> BasicMemoryConfig:
    return BasicMemoryConfig(
        env="test",
        projects={"test-project": ProjectEntry(path=str(tmp_path))},
        default_project="test-project",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
        semantic_vector_backend=SemanticVectorBackend.MILVUS,
        milvus_uri=str(tmp_path / "milvus.db"),
        milvus_token="test-token",
        semantic_embedding_sync_batch_size=8,
    )


def _row(
    *,
    project_id: int,
    row_id: int,
    entity_id: int,
    title: str,
    permalink: str,
    content: str,
) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        type=SearchItemType.ENTITY.value,
        title=title,
        permalink=permalink,
        file_path=f"{permalink}.md",
        metadata={"note_type": "spec"},
        entity_id=entity_id,
        content_stems=content,
        content_snippet=content,
        created_at=now,
        updated_at=now,
    )


def _repo(session_maker, test_project, tmp_path) -> SQLiteMilvusSearchRepository:
    return SQLiteMilvusSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=_milvus_config(tmp_path),
        embedding_provider=StubEmbeddingProvider(),
        milvus_client_factory=FakeMilvusClient,
        milvus_data_type=FakeDataType,
    )


@pytest.fixture(autouse=True)
def _reset_fake_milvus() -> None:
    FakeMilvusClient.reset()


@pytest.mark.asyncio
async def test_milvus_backend_creates_collection_and_marker_rows(
    session_maker,
    test_project,
    tmp_path,
):
    """Milvus backend stores real vectors externally and SQL marker rows locally."""
    repo = _repo(session_maker, test_project, tmp_path)

    await repo.init_search_index()
    await repo.bulk_index_items(
        [
            _row(
                project_id=test_project.id,
                row_id=101,
                entity_id=101,
                title="Authentication",
                permalink="specs/authentication",
                content="auth token session login",
            ),
            _row(
                project_id=test_project.id,
                row_id=102,
                entity_id=102,
                title="Database",
                permalink="specs/database",
                content="database schema migration",
            ),
        ]
    )

    result = await repo.sync_entity_vectors_batch([101, 102])

    assert result.entities_synced == 2
    assert FakeMilvusClient.instances[0].kwargs == {
        "uri": str(tmp_path / "milvus.db"),
        "token": "test-token",
    }
    collection = FakeMilvusClient.collections["basic_memory_vectors"]
    assert collection["indexes"] == [
        {
            "field_name": "embedding",
            "index_type": "AUTOINDEX",
            "metric_type": "COSINE",
        }
    ]
    assert collection["records"]

    async with db.scoped_session(session_maker) as session:
        marker_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_embeddings e "
                "JOIN search_vector_chunks c ON c.id = e.chunk_id "
                "WHERE c.project_id = :project_id"
            ),
            {"project_id": test_project.id},
        )
        assert int(marker_count.scalar_one()) == len(collection["records"])

    results = await repo.search(
        search_text="session token auth",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
        offset=0,
    )

    assert results
    assert results[0].permalink == "specs/authentication"


@pytest.mark.asyncio
async def test_milvus_backend_deletes_external_records_with_entity_chunks(
    session_maker,
    test_project,
    tmp_path,
):
    """Deleting entity vectors clears both SQL marker rows and Milvus records."""
    repo = _repo(session_maker, test_project, tmp_path)

    await repo.init_search_index()
    await repo.index_item(
        _row(
            project_id=test_project.id,
            row_id=201,
            entity_id=201,
            title="Authentication",
            permalink="specs/authentication",
            content="auth token session login",
        )
    )
    await repo.sync_entity_vectors(201)
    assert FakeMilvusClient.collections["basic_memory_vectors"]["records"]

    await repo.delete_entity_vector_rows(201)

    assert FakeMilvusClient.collections["basic_memory_vectors"]["records"] == {}
    async with db.scoped_session(session_maker) as session:
        chunk_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": 201},
        )
        marker_count = await session.execute(
            text("SELECT COUNT(*) FROM search_vector_embeddings"),
        )
        assert int(chunk_count.scalar_one()) == 0
        assert int(marker_count.scalar_one()) == 0


def test_create_search_repository_selects_sqlite_milvus_backend(tmp_path):
    """Factory should keep SQL backend selection but swap semantic vector storage."""
    config = _milvus_config(tmp_path)
    config.semantic_search_enabled = False

    repo = create_search_repository(cast(Any, None), project_id=1, app_config=config)

    assert isinstance(repo, SQLiteMilvusSearchRepository)


@pytest.mark.asyncio
async def test_milvus_backend_reports_missing_optional_dependency(
    session_maker,
    test_project,
    tmp_path,
    monkeypatch,
):
    """Selecting Milvus without pymilvus should fail with the semantic dependency error."""
    repo = SQLiteMilvusSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=_milvus_config(tmp_path),
        embedding_provider=StubEmbeddingProvider(),
    )

    def _missing_pymilvus(name: str) -> Any:
        if name == "pymilvus":
            raise ImportError(name)
        return __import__(name)

    monkeypatch.setattr(
        "basic_memory.repository.milvus_search_repository.importlib.import_module",
        _missing_pymilvus,
    )

    with pytest.raises(SemanticDependenciesMissingError):
        await repo._ensure_vector_tables()
