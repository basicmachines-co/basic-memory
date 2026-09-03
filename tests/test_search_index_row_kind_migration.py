"""Tests for the search_index row-kind uniqueness migration (#1437)."""

from importlib import import_module
from types import SimpleNamespace


migration = import_module(
    "basic_memory.alembic.versions.w6k7i8n9d0a1_search_index_row_kind_uniqueness"
)


def _connection(dialect_name: str) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))


def _statements(monkeypatch, dialect_name: str, run) -> list[str]:
    executed: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _connection(dialect_name))
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))
    run()
    return executed


def test_upgrade_widens_the_unique_index_to_include_the_row_kind(monkeypatch) -> None:
    statements = _statements(monkeypatch, "postgresql", migration.upgrade)

    assert len(statements) == 2
    # The wide index is created before the narrow one is dropped, so the table is never
    # momentarily unconstrained.
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_type_project"
        in (statements[0])
    )
    assert "ON search_index (permalink, type, project_id)" in statements[0]
    assert "WHERE permalink IS NOT NULL" in statements[0]
    assert statements[1] == "DROP INDEX IF EXISTS uix_search_index_permalink_project"


def test_downgrade_sheds_the_rows_the_narrow_index_cannot_admit(monkeypatch) -> None:
    statements = _statements(monkeypatch, "postgresql", migration.downgrade)

    assert len(statements) == 3
    # The narrow index cannot be recreated while one permalink is held by two kinds,
    # so the extra derived projections go first, keeping entity over observation over
    # relation -- which is what ordering by `type` gives.
    assert "DELETE FROM search_index" in statements[0]
    assert "PARTITION BY project_id, permalink" in statements[0]
    assert "ORDER BY type, id" in statements[0]
    assert "row_rank > 1" in statements[0]
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uix_search_index_permalink_project" in statements[1]
    assert "ON search_index (permalink, project_id)" in statements[1]
    assert statements[2] == "DROP INDEX IF EXISTS uix_search_index_permalink_type_project"


def test_migration_is_noop_for_sqlite(monkeypatch) -> None:
    """SQLite's search_index is an FTS5 virtual table: it has no unique index to change."""
    assert _statements(monkeypatch, "sqlite", migration.upgrade) == []
    assert _statements(monkeypatch, "sqlite", migration.downgrade) == []
