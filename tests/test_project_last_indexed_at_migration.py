"""Migration tests for project.last_indexed_at (#1414)."""

import sqlite3

from alembic import command

from basic_memory.alembic.versions import (  # type: ignore[attr-defined]
    w6r7e8a9d0y1_add_project_last_indexed_at as migration,
)
from tests.test_note_content_migration import sqlite_alembic_config

# Pin the downgrade target to this migration's own parent. A relative "-1" would
# instead undo whichever migration currently sits at head, so the test would break
# every time a later revision lands.
DOWN_REVISION: str = str(migration.down_revision)


def _project_row(connection: sqlite3.Connection, name: str) -> tuple[str | None]:
    return connection.execute(
        "SELECT last_indexed_at FROM project WHERE name = ?", (name,)
    ).fetchone()


def _insert_project(connection: sqlite3.Connection, project_id: int, name: str) -> None:
    connection.execute(
        "INSERT INTO project (id, external_id, name, permalink, path, is_active, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1, '2026-01-01 00:00:00', '2026-02-02 00:00:00')",
        (project_id, f"external-{project_id}", name, name, f"/tmp/{name}"),
    )


def _insert_entity(connection: sqlite3.Connection, project_id: int) -> None:
    connection.execute(
        "INSERT INTO entity (external_id, title, note_type, content_type, project_id, "
        "permalink, file_path, created_at, updated_at) "
        "VALUES (?, 'Note', 'note', 'text/markdown', ?, ?, ?, "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')",
        (f"entity-{project_id}", project_id, f"note-{project_id}", f"note-{project_id}.md"),
    )


def test_upgrade_adds_a_nullable_marker(tmp_path, monkeypatch):
    """The column exists and defaults to NULL -- "no pass has ever completed"."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "last-indexed-at.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        column = next(
            row
            for row in connection.execute("PRAGMA table_info(project)").fetchall()
            if row[1] == "last_indexed_at"
        )
        # notnull is column index 3; the marker must be nullable to mean "never".
        assert column[3] == 0
    finally:
        connection.close()


def test_upgrade_backfills_projects_that_already_hold_entities(tmp_path, monkeypatch):
    """An upgrade must not newly describe every existing project as never indexed.

    A project with entities demonstrably has an index. Leaving it NULL would make
    the readiness contract lie about every project on the machine the moment the
    migration lands.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "backfill.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, DOWN_REVISION)

    connection = sqlite3.connect(database_path)
    try:
        _insert_project(connection, 1, "indexed-already")
        _insert_project(connection, 2, "empty-project")
        _insert_entity(connection, 1)
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        # Backfilled from updated_at: a lower bound on when indexing happened,
        # which is all this column needs to be -- only NULL-versus-not matters.
        assert _project_row(connection, "indexed-already")[0] is not None
        # A project with nothing indexed keeps the honest answer.
        assert _project_row(connection, "empty-project")[0] is None
    finally:
        connection.close()


def test_downgrade_drops_the_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "downgrade.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, DOWN_REVISION)

    connection = sqlite3.connect(database_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()}
        assert "last_indexed_at" not in columns
    finally:
        connection.close()
