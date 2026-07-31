"""Real Milvus Lite contract verification."""

from __future__ import annotations

import subprocess
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


_RESTART_SCRIPT = """
import asyncio
import sys

from basic_memory.repository.milvus_config import MilvusSettings
from basic_memory.repository.milvus_index import MilvusVectorIndex
from basic_memory.repository.semantic_vector_index import (
    VectorIndexScope,
    VectorKey,
    VectorRecord,
)


async def main() -> None:
    phase, database_path = sys.argv[1:]
    scope = VectorIndexScope(
        namespace="restart-database",
        project_id=7,
        embedding_identity="Stub:model",
        dimensions=3,
    )
    key = VectorKey(entity_id=1, chunk_key="summary:0")
    index = MilvusVectorIndex(scope, MilvusSettings(uri=database_path))

    if phase == "write":
        await index.upsert(
            [
                VectorRecord(
                    key=key,
                    source_hash="auth-v1",
                    values=(1.0, 0.0, 0.0),
                )
            ]
        )
        return

    await index.delete_orphans([key])
    matches = await index.search((1.0, 0.0, 0.0), limit=1)
    assert [match.key for match in matches] == [key]


asyncio.run(main())
"""


def _run_restart_phase(phase: str, database_path: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _RESTART_SCRIPT, phase, database_path],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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


def test_milvus_lite_reloads_collection_after_process_restart(tmp_path) -> None:
    database_path = str(tmp_path / "restart-vectors.db")

    _run_restart_phase("write", database_path)
    _run_restart_phase("read", database_path)
