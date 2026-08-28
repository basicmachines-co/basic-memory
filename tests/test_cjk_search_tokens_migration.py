"""Tests for the CJK search token schema migration."""

from collections.abc import Callable
import sqlite3
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from alembic import command
from alembic.config import Config
import pytest

from basic_memory import db
from tests import conftest as test_conftest

migration = import_module("basic_memory.alembic.versions.8a7b6c5d4e3f_add_cjk_search_tokens")


# Column set on the SQLite FTS5 search_index table immediately before this
# migration (i.e. the schema produced by models/search.py's CREATE_SEARCH_INDEX
# as of revision 7f6a2b8c9d10). Used to assert the migration adds exactly one
# new column and that downgrade restores exactly this set.
PRE_MIGRATION_SEARCH_INDEX_COLUMNS = {
    "id",
    "title",
    "content_stems",
    "content_snippet",
    "permalink",
    "file_path",
    "type",
    "project_id",
    "from_id",
    "to_id",
    "relation_type",
    "entity_id",
    "category",
    "metadata",
    "created_at",
    "updated_at",
}


def _sqlite_alembic_config(database_path: Path) -> Config:
    """Build an Alembic config that upgrades a temporary SQLite database."""
    alembic_dir = Path(db.__file__).parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option("revision_environment", "false")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _connection(dialect_name: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))


class _FakeResult:
    def scalar(self) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def run_sync(self, callback: Callable[..., object]) -> None:
        del callback

    async def execute(self, statement: object) -> _FakeResult:
        self.statements.append(statement)
        return _FakeResult()


class _FakeTransaction:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self.connection)


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}


# --- SQLite: real database, real Alembic upgrade/downgrade ---


def _seed_legacy_search_index_with_data(connection: sqlite3.Connection) -> None:
    """Simulate a database where SearchRepository.init_search_index already
    created the runtime FTS5 table (pre-migration shape) with a row in it,
    alongside the canonical project/entity rows it was derived from."""
    timestamp = "2026-08-29 00:00:00"
    connection.execute(
        """
        INSERT INTO project (
            id, name, permalink, path, is_active, is_default,
            created_at, updated_at, external_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "test", "test", "/test", True, True, timestamp, timestamp, "project-1"),
    )
    connection.execute(
        """
        INSERT INTO entity (
            id, title, note_type, content_type, file_path,
            created_at, updated_at, project_id, external_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "Source", "note", "text/markdown", "source.md", timestamp, timestamp, 1, "entity-1"),
    )
    connection.execute(
        "CREATE VIRTUAL TABLE search_index USING fts5("
        "id UNINDEXED, title, content_stems, content_snippet, permalink, "
        "file_path UNINDEXED, type UNINDEXED, project_id UNINDEXED, "
        "from_id UNINDEXED, to_id UNINDEXED, relation_type UNINDEXED, "
        "entity_id UNINDEXED, category UNINDEXED, metadata UNINDEXED, "
        "created_at UNINDEXED, updated_at UNINDEXED)"
    )
    connection.execute(
        "INSERT INTO search_index (id, title, type, project_id) VALUES (1, 'Source', 'entity', 1)"
    )
    connection.commit()


def test_sqlite_upgrade_skips_missing_search_index(tmp_path, monkeypatch) -> None:
    """A fresh install has no runtime FTS5 table yet; the migration must not
    create one -- that stays SearchRepository.init_search_index's job, and it
    will build the table with search_tokens already present."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "cjk-search-tokens-fresh.db"
    config = _sqlite_alembic_config(database_path)

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        search_index_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'search_index'"
        ).fetchone()
    finally:
        connection.close()
    assert search_index_exists is None


def test_sqlite_upgrade_adds_search_tokens_and_preserves_source_data(tmp_path, monkeypatch) -> None:
    """Recreating an existing derived FTS5 index adds search_tokens without
    disturbing canonical source rows."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "cjk-search-tokens-data.db"
    config = _sqlite_alembic_config(database_path)
    command.upgrade(config, "7f6a2b8c9d10")
    connection = sqlite3.connect(database_path)
    try:
        _seed_legacy_search_index_with_data(connection)
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        entity_rows = connection.execute("SELECT id, title FROM entity").fetchall()
        project_rows = connection.execute("SELECT id, name FROM project").fetchall()
        # The derived index itself is legitimately emptied by the recreate;
        # only the canonical entity/project rows must survive untouched.
        search_rows = connection.execute("SELECT id FROM search_index").fetchall()
        columns = _table_columns(connection, "search_index")
    finally:
        connection.close()
    assert entity_rows == [(1, "Source")]
    assert project_rows == [(1, "test")]
    assert search_rows == []
    assert columns == PRE_MIGRATION_SEARCH_INDEX_COLUMNS | {"search_tokens"}


def test_sqlite_downgrade_removes_search_tokens_column(tmp_path, monkeypatch) -> None:
    """Downgrade must restore exactly the pre-migration column set and must
    not disturb canonical source rows either."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "cjk-search-tokens-downgrade.db"
    config = _sqlite_alembic_config(database_path)
    command.upgrade(config, "7f6a2b8c9d10")
    connection = sqlite3.connect(database_path)
    try:
        _seed_legacy_search_index_with_data(connection)
    finally:
        connection.close()
    command.upgrade(config, "head")

    command.downgrade(config, "7f6a2b8c9d10")

    connection = sqlite3.connect(database_path)
    try:
        columns = _table_columns(connection, "search_index")
        entity_rows = connection.execute("SELECT id, title FROM entity").fetchall()
    finally:
        connection.close()
    assert columns == PRE_MIGRATION_SEARCH_INDEX_COLUMNS
    assert "search_tokens" not in columns
    assert entity_rows == [(1, "Source")]


# --- PostgreSQL: dialect-branch unit tests (no live database required) ---
#
# Mirrors the monkeypatch technique in test_postgres_full_content_search_migration.py,
# the test for this migration's own down_revision (7f6a2b8c9d10): capture the
# literal SQL passed to op.execute rather than asserting against a live
# information_schema/pg_indexes query, since standing up Postgres here would
# require Docker/testcontainers.


def _normalized_statements(statements: list[str]) -> list[str]:
    """Collapse each captured statement's whitespace so line-wrapping in the
    migration source can't change whether an assertion matches."""
    return [" ".join(statement.split()) for statement in statements]


def test_postgres_upgrade_adds_token_columns_vectors_and_gin_indexes(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.upgrade()

    normalized = _normalized_statements(statements)
    assert "ALTER TABLE search_index ADD COLUMN IF NOT EXISTS search_tokens TEXT" in normalized
    assert any(
        "search_tokens_index_col tsvector GENERATED ALWAYS AS" in statement
        and "to_tsvector('simple', coalesce(search_tokens, ''))" in statement
        and "STORED" in statement
        for statement in normalized
    )
    assert (
        "CREATE INDEX IF NOT EXISTS idx_search_index_cjk_fts "
        "ON search_index USING gin(search_tokens_index_col)" in normalized
    )

    assert (
        "ALTER TABLE search_index_fts_chunks ADD COLUMN IF NOT EXISTS chunk_tokens TEXT"
        in normalized
    )
    assert any(
        "chunk_tokens_index_col tsvector GENERATED ALWAYS AS" in statement
        and "to_tsvector('simple', coalesce(chunk_tokens, ''))" in statement
        and "STORED" in statement
        for statement in normalized
    )
    assert (
        "CREATE INDEX IF NOT EXISTS idx_search_index_fts_chunks_cjk_fts "
        "ON search_index_fts_chunks USING gin(chunk_tokens_index_col)" in normalized
    )

    # Exactly two columns + one index per table (row-level, then chunk-level);
    # nothing else runs on the postgresql branch.
    assert len(normalized) == 6


def test_postgres_downgrade_removes_only_new_columns_and_indexes(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.downgrade()

    normalized = _normalized_statements(statements)
    assert "DROP INDEX IF EXISTS idx_search_index_cjk_fts" in normalized
    assert "DROP INDEX IF EXISTS idx_search_index_fts_chunks_cjk_fts" in normalized
    assert "ALTER TABLE search_index DROP COLUMN IF EXISTS search_tokens_index_col" in normalized
    assert "ALTER TABLE search_index DROP COLUMN IF EXISTS search_tokens" in normalized
    assert (
        "ALTER TABLE search_index_fts_chunks DROP COLUMN IF EXISTS chunk_tokens_index_col"
        in normalized
    )
    assert "ALTER TABLE search_index_fts_chunks DROP COLUMN IF EXISTS chunk_tokens" in normalized

    # Exactly the two new indexes and four new columns come off; a stray
    # seventh statement would mean a pre-existing column/index got touched.
    assert len(normalized) == 6


@pytest.mark.asyncio
async def test_postgres_fixture_recreates_cjk_indexes_without_docker(monkeypatch) -> None:
    """The shared Postgres fixture must execute both CJK index DDL statements."""
    connection = _FakeConnection()
    monkeypatch.setattr(test_conftest.command, "stamp", lambda *args, **kwargs: None)

    await test_conftest._reset_postgres_test_schema(
        cast(test_conftest.AsyncEngine, _FakeEngine(connection)),
        "postgresql+asyncpg://test/test",
    )

    normalized = [" ".join(str(statement).split()) for statement in connection.statements]
    assert any("idx_search_index_cjk_fts" in statement for statement in normalized)
    assert any("idx_search_index_fts_chunks_cjk_fts" in statement for statement in normalized)
