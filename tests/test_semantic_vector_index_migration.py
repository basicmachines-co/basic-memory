"""Tests for semantic vector manifest state migration behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from basic_memory.alembic.versions import (
    o8j9k0l1m2n3_add_vector_index_manifest_state as migration,
)


def _connection(dialect: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect))


def test_upgrade_backfills_ready_state_from_existing_pgvector_rows(monkeypatch) -> None:
    connection = _connection("postgresql")
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "search_vector_chunks",
        "search_vector_embeddings",
    ]
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
    assert any("SET vector_index = 'pgvector'" in sql for sql in statements)
    assert any("WHEN EXISTS" in sql and "THEN 'ready'" in sql for sql in statements)
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
    inspector.get_table_names.return_value = ["search_vector_chunks"]
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "create_check_constraint", MagicMock())

    migration.upgrade()

    statements = [call.args[0] for call in execute.call_args_list]
    assert "UPDATE search_vector_chunks SET embedding_status = 'pending'" in statements


def test_upgrade_is_noop_for_sqlite_or_missing_manifest(monkeypatch) -> None:
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "create_check_constraint", MagicMock())

    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("sqlite"))
    migration.upgrade()

    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(migration, "inspect", lambda _connection: inspector)
    migration.upgrade()

    execute.assert_not_called()


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
