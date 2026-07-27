"""Real Milvus Lite contract verification."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("pymilvus", reason="install basic-memory[milvus] to test Milvus")

from basic_memory.repository.milvus_config import MilvusSettings
from basic_memory.repository.milvus_index import MilvusVectorIndex
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorRecord,
)

pytestmark = [
    pytest.mark.semantic,
    pytest.mark.skipif(sys.platform == "win32", reason="Milvus Lite does not support Windows"),
]


@pytest.mark.asyncio
async def test_milvus_lite_vector_lifecycle(tmp_path) -> None:
    scope = VectorIndexScope(
        namespace="integration-database",
        project_id=7,
        embedding_identity="Stub:model",
        dimensions=3,
    )
    index = MilvusVectorIndex(
        scope,
        MilvusSettings(uri=str(tmp_path / "vectors.db")),
    )
    auth_key = VectorKey(entity_id=1, chunk_key="summary:0")
    database_key = VectorKey(entity_id=2, chunk_key="summary:0")
    await index.upsert(
        [
            VectorRecord(
                key=auth_key,
                source_hash="auth-v1",
                values=(1.0, 0.0, 0.0),
            ),
            VectorRecord(
                key=database_key,
                source_hash="database-v1",
                values=(0.0, 1.0, 0.0),
            ),
        ]
    )

    matches = await index.search((1.0, 0.0, 0.0), limit=2)
    assert matches[0].key == auth_key
    assert matches[0].similarity == pytest.approx(1.0)

    await index.delete([VectorDeletion(key=auth_key, source_hash="stale-generation")])
    assert (await index.search((1.0, 0.0, 0.0), limit=2))[0].key == auth_key

    await index.delete([VectorDeletion(key=auth_key, source_hash="auth-v1")])
    assert [match.key for match in await index.search((1.0, 0.0, 0.0), limit=2)] == [database_key]

    await index.delete_orphans([])
    assert await index.search((1.0, 0.0, 0.0), limit=2) == []
