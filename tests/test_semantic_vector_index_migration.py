"""Tests for semantic vector manifest state migration behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from alembic import command
from alembic.config import Config

from basic_memory import db
from basic_memory.alembic.versions import (
    p9k0l1m2n3o4_add_vector_index_manifest_state as migration,
)


def _connection(dialect: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect))


def _sqlite_alembic_config(database_path: Path) -> Config:
    alembic_dir = Path(db.__file__).parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option("revision_environment", "false")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_upgrade_backfills_ready_state_from_existing_pgvector_rows(monkeypatch) -> None:
    connection = _connection("postgresql")
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "note_file_vacate",
        "search_vector_chunks",
        "search_vector_embeddings",
    ]
    inspector.get_check_constraints.return_value = []
    execute = MagicMock()
    create_check_constraint = MagicMock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "create_check_constraint", create_check_constraint)

    migration.upgrade()

    statements = [call.args[0] for call in execute.call_args_list]
    assert any("ADD COLUMN IF NOT EXISTS vector_index" in sql for sql in statements)
    assert any("ADD COLUMN IF NOT EXISTS embedding_status" in sql for sql in statements)
    assert any(
        "SET vector_index = 'pgvector'" in sql and "WHERE vector_index IS NULL" in sql
        for sql in statements
    )
    assert any("WHEN EXISTS" in sql and "THEN 'ready'" in sql for sql in statements)
    assert any("WHERE embedding_status IS NULL" in sql for sql in statements)
    assert any("ALTER COLUMN vector_index SET NOT NULL" in sql for sql in statements)
    assert any("ALTER COLUMN embedding_status SET NOT NULL" in sql for sql in statements)
    create_check_constraint.assert_called_once_with(
        "ck_search_vector_chunks_embedding_status",
        "search_vector_chunks",
        "embedding_status IN ('pending', 'ready')",
    )


def test_upgrade_marks_pending_without_embedding_table(monkeypatch) -> None:
    connection = _connection("postgresql")
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["note_file_vacate", "search_vector_chunks"]
    inspector.get_check_constraints.return_value = []
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "create_check_constraint", MagicMock())

    migration.upgrade()

    statements = [call.args[0] for call in execute.call_args_list]
    assert any(
        "SET embedding_status = 'pending'" in sql and "WHERE embedding_status IS NULL" in sql
        for sql in statements
    )


def test_upgrade_skips_manifest_for_sqlite_or_missing_postgres_table(monkeypatch) -> None:
    execute = MagicMock()
    create_table = MagicMock()
    create_index = MagicMock()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "create_table", create_table)
    monkeypatch.setattr(migration.op, "create_index", create_index)
    monkeypatch.setattr(migration.op, "create_check_constraint", MagicMock())

    sqlite_inspector = MagicMock()
    sqlite_inspector.get_table_names.return_value = ["note_file_vacate"]
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("sqlite"))
    monkeypatch.setattr(migration, "inspect", lambda _connection: sqlite_inspector)
    migration.upgrade()

    postgres_inspector = MagicMock()
    postgres_inspector.get_table_names.return_value = ["note_file_vacate"]
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(migration, "inspect", lambda _connection: postgres_inspector)
    migration.upgrade()

    execute.assert_not_called()
    create_table.assert_not_called()
    create_index.assert_not_called()


def test_upgrade_repairs_postgres_state_from_duplicate_vector_revision(monkeypatch) -> None:
    connection = _connection("postgresql")
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "search_vector_chunks",
        "search_vector_embeddings",
    ]
    inspector.get_check_constraints.return_value = [
        {"name": "ck_search_vector_chunks_embedding_status"}
    ]
    create_table = MagicMock()
    create_index = MagicMock()
    create_check_constraint = MagicMock()
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    monkeypatch.setattr(migration.op, "create_table", create_table)
    monkeypatch.setattr(migration.op, "create_index", create_index)
    monkeypatch.setattr(migration.op, "create_check_constraint", create_check_constraint)
    monkeypatch.setattr(migration.op, "execute", execute)

    migration.upgrade()

    assert create_table.call_args.args[0] == "note_file_vacate"
    create_index.assert_called_once_with(
        "ix_note_file_vacate_project_id",
        "note_file_vacate",
        ["project_id"],
        unique=False,
    )
    create_check_constraint.assert_not_called()
    statements = [call.args[0] for call in execute.call_args_list]
    assert any("WHERE vector_index IS NULL" in sql for sql in statements)
    assert any("WHERE embedding_status IS NULL" in sql for sql in statements)


def test_upgrade_repairs_sqlite_state_from_duplicate_vector_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "duplicate-vector-revision.db"
    config = _sqlite_alembic_config(database_path)
    command.upgrade(config, "n7i8j9k0l1m2")
    command.stamp(config, "o8j9k0l1m2n3")

    connection = sqlite3.connect(database_path)
    try:
        tables_before = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert "note_file_vacate" not in tables_before

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(note_file_vacate)").fetchall()
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(note_file_vacate)").fetchall()
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert columns == {
        "id",
        "project_id",
        "entity_id",
        "file_path",
        "file_checksum",
        "created_at",
    }
    assert "ix_note_file_vacate_project_id" in indexes
    assert version == ("p9k0l1m2n3o4",)


def test_downgrade_removes_manifest_state(monkeypatch) -> None:
    connection = _connection("postgresql")
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["search_vector_chunks"]
    execute = MagicMock()
    drop_constraint = MagicMock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "drop_constraint", drop_constraint)

    migration.downgrade()

    drop_constraint.assert_called_once_with(
        "ck_search_vector_chunks_embedding_status",
        "search_vector_chunks",
        type_="check",
    )
    statements = [call.args[0] for call in execute.call_args_list]
    assert any("DROP COLUMN IF EXISTS embedding_status" in sql for sql in statements)
    assert any("DROP COLUMN IF EXISTS vector_index" in sql for sql in statements)


def test_downgrade_is_noop_for_sqlite_or_missing_manifest(monkeypatch) -> None:
    execute = MagicMock()
    drop_constraint = MagicMock()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "drop_constraint", drop_constraint)

    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("sqlite"))
    migration.downgrade()

    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    migration.downgrade()

    execute.assert_not_called()
    drop_constraint.assert_not_called()
