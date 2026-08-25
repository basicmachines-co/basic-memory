"""Tests for the PostgreSQL full-content FTS migration."""

from importlib import import_module
from types import SimpleNamespace


migration = import_module(
    "basic_memory.alembic.versions.7f6a2b8c9d10_index_full_postgres_note_content"
)


def _connection(dialect_name: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))


def test_upgrade_rebuilds_postgres_vector_with_full_content(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.upgrade()

    generated_column = next(
        statement for statement in statements if "ADD COLUMN textsearchable_index_col" in statement
    )
    assert "coalesce(content_stems, '')" in generated_column
    assert "coalesce(content_snippet, '')" in generated_column
    assert statements[-1].startswith("CREATE INDEX idx_search_index_fts")


def test_downgrade_restores_capped_postgres_vector(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("postgresql"))
    monkeypatch.setattr(
        migration.op, "execute", lambda statement: statements.append(str(statement))
    )

    migration.downgrade()

    generated_column = next(
        statement for statement in statements if "ADD COLUMN textsearchable_index_col" in statement
    )
    assert "coalesce(content_stems, '')" in generated_column
    assert "content_snippet" not in generated_column


def test_migration_is_noop_for_sqlite(monkeypatch) -> None:
    execute_calls: list[object] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection("sqlite"))
    monkeypatch.setattr(migration.op, "execute", execute_calls.append)

    migration.upgrade()
    migration.downgrade()

    assert execute_calls == []
