"""Tests for the PostgreSQL full-content FTS migration."""

from importlib import import_module
from types import SimpleNamespace


migration = import_module(
    "basic_memory.alembic.versions.7f6a2b8c9d10_index_full_postgres_note_content"
)


def _connection(dialect_name: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))


def test_upgrade_creates_and_backfills_bounded_postgres_vectors(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.upgrade()

    # A database whose search_index was removed by the destructive pre-v0.23.1
    # reindex is repaired first; healthy databases no-op on IF NOT EXISTS.
    assert "CREATE TABLE IF NOT EXISTS search_index (" in statements[0]
    assert "PRIMARY KEY (id, type, project_id)" in statements[0]
    assert "CREATE INDEX IF NOT EXISTS ix_search_index_project_id" in statements[1]
    assert "ON search_index (project_id)" in statements[1]
    assert "CREATE INDEX IF NOT EXISTS idx_search_index_fts" in statements[2]
    assert "CREATE INDEX IF NOT EXISTS idx_search_index_metadata_gin" in statements[3]
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_project" in statements[4]

    assert "CREATE TABLE search_index_fts_chunks" in statements[5]
    assert "to_tsvector('english', chunk_text)" in statements[5]
    assert "generate_series" in statements[6]
    assert "FOR 8000" in statements[6]
    assert "5952" in statements[6]
    assert "2,048-character overlap" in statements[6]
    assert "CREATE INDEX idx_search_index_fts_chunks_fts" in statements[7]


def test_downgrade_drops_postgres_chunk_table(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.downgrade()

    assert statements == ["DROP TABLE IF EXISTS search_index_fts_chunks"]


def test_migration_is_noop_for_sqlite(monkeypatch) -> None:
    execute_calls: list[object] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("sqlite"))
    monkeypatch.setattr(migration.op, "execute", execute_calls.append)

    migration.upgrade()
    migration.downgrade()

    assert execute_calls == []
