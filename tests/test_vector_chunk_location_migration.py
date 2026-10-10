"""Re-keying vector chunks by content keeps every existing embedding (0040).

Chunk keys used to embed the search_index row id ("observation:500:0"). The upgrade
moves that location into columns and re-keys each chunk by its text. Built-in vector
storage finds embeddings by chunk row id, so the upgrade must keep every row id: a
changed id would orphan the embedding and force a re-embed of the whole database.
"""

import hashlib
from importlib import import_module

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from basic_memory.models.search import SQLITE_CHUNK_LOCATION_UPGRADE
from basic_memory.repository.semantic_chunking import build_vector_chunk_records

migration = import_module("basic_memory.alembic.versions.0040_vector_chunk_location_columns")

# The chunk table as it existed before the location columns, per backend.
LEGACY_POSTGRES_CHUNKS = """
CREATE TABLE search_vector_chunks (
    id BIGSERIAL PRIMARY KEY,
    entity_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    entity_fingerprint TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_index TEXT NOT NULL,
    embedding_status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, entity_id, chunk_key)
)
"""
LEGACY_SQLITE_CHUNKS = """
CREATE TABLE search_vector_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    entity_fingerprint TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_index TEXT NOT NULL,
    embedding_status TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, entity_id, chunk_key)
)
"""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# id, legacy key, chunk text, vector index. The two observations repeat the same
# text, so they must get distinct occurrence numbers.
LEGACY_ROWS = [
    (10, "entity:1:0", "Ledger body", "sqlite-vec"),
    (11, "entity:1:1", "Ledger body continued", "sqlite-vec"),
    (12, "observation:500:0", "decision: Repeated fact", "pgvector"),
    (13, "observation:501:0", "decision: Repeated fact", "pgvector"),
    (14, "relation:700:0", "relates_to Indexing Pipeline", "milvus"),
]


def _upgrade_postgres(connection) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()


@pytest.mark.asyncio
async def test_upgrade_rekeys_by_content_and_keeps_row_ids(engine_factory, db_backend):
    engine, _ = engine_factory
    async with engine.begin() as connection:
        # Postgres keeps pgvector embeddings in a table that references the chunks.
        cascade = " CASCADE" if db_backend == "postgres" else ""
        await connection.execute(text(f"DROP TABLE IF EXISTS search_vector_chunks{cascade}"))
        await connection.execute(
            text(LEGACY_POSTGRES_CHUNKS if db_backend == "postgres" else LEGACY_SQLITE_CHUNKS)
        )
        await connection.execute(
            text(
                "INSERT INTO search_vector_chunks (id, entity_id, project_id, chunk_key, "
                "chunk_text, source_hash, entity_fingerprint, embedding_model, vector_index, "
                "embedding_status) VALUES (:id, 1, 1, :chunk_key, :chunk_text, :source_hash, "
                "'fingerprint', 'model', :vector_index, 'ready')"
            ),
            [
                {
                    "id": row_id,
                    "chunk_key": chunk_key,
                    "chunk_text": chunk_text,
                    "source_hash": _sha(chunk_text),
                    "vector_index": vector_index,
                }
                for row_id, chunk_key, chunk_text, vector_index in LEGACY_ROWS
            ],
        )

    async with engine.begin() as connection:
        if db_backend == "postgres":
            await connection.run_sync(_upgrade_postgres)
        else:
            for statement in SQLITE_CHUNK_LOCATION_UPGRADE:
                await connection.execute(text(statement))

    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT id, chunk_key, source_type, source_row_id, chunk_index, "
                "embedding_status FROM search_vector_chunks ORDER BY id"
            )
        )
        rows = {int(row.id): row for row in result}

    assert sorted(rows) == [10, 11, 12, 13, 14]
    expected = {
        10: ("entity", 1, 0, f"entity:{_sha('Ledger body')}:0"),
        11: ("entity", 1, 1, f"entity:{_sha('Ledger body continued')}:0"),
        12: ("observation", 500, 0, f"observation:{_sha('decision: Repeated fact')}:0"),
        13: ("observation", 501, 0, f"observation:{_sha('decision: Repeated fact')}:1"),
        14: ("relation", 700, 0, f"relation:{_sha('relates_to Indexing Pipeline')}:0"),
    }
    for row_id, (source_type, source_row_id, chunk_index, chunk_key) in expected.items():
        row = rows[row_id]
        assert (row.source_type, row.source_row_id, row.chunk_index, row.chunk_key) == (
            source_type,
            source_row_id,
            chunk_index,
            chunk_key,
        )

    # Built-in indexes keep their vectors; the external index stored the old key.
    assert rows[12].embedding_status == "ready"
    assert rows[14].embedding_status == "pending"


def test_upgrade_keys_match_what_vector_sync_builds():
    """The migration and the runtime number repeated text the same way.

    If they disagreed, the first sync after upgrade would see new keys, find no
    match, and re-embed exactly the chunks the migration meant to keep.
    """

    class Row:
        def __init__(self, row_id: int, row_type: str, snippet: str) -> None:
            self.id = row_id
            self.type = row_type
            self.title = None
            self.permalink = None
            self.content_snippet = snippet
            self.category = None
            self.relation_type = None

    records = build_vector_chunk_records(
        [
            Row(500, "observation", "decision: Repeated fact"),
            Row(501, "observation", "decision: Repeated fact"),
        ]
    ).records

    by_row = {record["source_row_id"]: record["chunk_key"] for record in records}
    sha = _sha("decision: Repeated fact")
    assert by_row == {500: f"observation:{sha}:0", 501: f"observation:{sha}:1"}
