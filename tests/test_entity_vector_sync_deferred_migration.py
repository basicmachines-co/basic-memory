"""Migration tests for entity.vector_sync_deferred_at (#1440 review)."""

import sqlite3

from alembic import command

from basic_memory.alembic.versions import (  # type: ignore[attr-defined]
    x7d8e9f0a1b2_add_entity_vector_sync_deferred_at as migration,
)
from tests.test_note_content_migration import sqlite_alembic_config

DOWN_REVISION: str = str(migration.down_revision)


def _entity_columns(connection: sqlite3.Connection) -> list[str]:
    return [row[1] for row in connection.execute("PRAGMA table_info(entity)").fetchall()]


def test_upgrade_adds_a_nullable_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "deferred.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        column = next(
            row
            for row in connection.execute("PRAGMA table_info(entity)").fetchall()
            if row[1] == "vector_sync_deferred_at"
        )
        # notnull is index 3; NULL has to mean "owes no deferred work".
        assert column[3] == 0
    finally:
        connection.close()


def test_downgrade_drops_the_marker_without_disturbing_generated_columns(tmp_path, monkeypatch):
    """The `entity` table carries `sa.Computed` columns.

    SQLite's batch mode would recreate the table to drop a column and emit those
    twice ("duplicate column name: frontmatter_status"), breaking this migration
    and every downgrade routed through it. A plain ALTER TABLE avoids the
    recreation, so this asserts the table came back intact -- no duplicated
    column -- rather than only that the marker is gone.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "downgrade.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, DOWN_REVISION)

    connection = sqlite3.connect(database_path)
    try:
        columns = _entity_columns(connection)
        assert "vector_sync_deferred_at" not in columns
        assert len(columns) == len(set(columns)), f"entity has duplicated columns: {columns}"
    finally:
        connection.close()
