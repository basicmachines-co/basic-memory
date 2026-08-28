"""Migration tests for the relation.to_id ON DELETE change.

The rest of the SQLite suite builds its schema straight from SQLAlchemy metadata, so a
corrected model with a missing or wrong migration would sail through green while every
database already on disk kept the old CASCADE. These tests run the real Alembic chain
and read the constraint back out.
"""

import sqlite3
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config

from basic_memory import db


migration = import_module(
    "basic_memory.alembic.versions.bcdbd5a942ca_relation_to_id_set_null_on_delete"
)

# Read off the module so a renamed revision breaks here rather than drifting silently.
REVISION: str = str(migration.revision)
DOWN_REVISION: str = str(migration.down_revision)


def sqlite_alembic_config(database_path: Path) -> Config:
    """Build an Alembic config that upgrades a temporary SQLite database."""
    alembic_dir = Path(db.__file__).parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option(
        "file_template",
        "%%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s",
    )
    config.set_main_option("timezone", "UTC")
    config.set_main_option("revision_environment", "false")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def _relation_fk_actions(database_path: Path) -> dict[str, str]:
    """Map relation's foreign key columns to the ON DELETE action SQLite recorded."""
    connection = sqlite3.connect(database_path)
    try:
        # PRAGMA columns: id, seq, table, from, to, on_update, on_delete, match
        return {
            str(row[3]): str(row[6])
            for row in connection.execute("PRAGMA foreign_key_list(relation)")
        }
    finally:
        connection.close()


def test_sqlite_upgrade_sets_relation_to_id_null_on_delete(tmp_path, monkeypatch):
    """A migrated SQLite database must unresolve inbound relations, not delete them."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "relation-to-id-set-null.db"
    config = sqlite_alembic_config(database_path)

    command.upgrade(config, DOWN_REVISION)
    before = _relation_fk_actions(database_path)
    assert before["to_id"] == "CASCADE"

    command.upgrade(config, REVISION)
    after = _relation_fk_actions(database_path)

    assert after["to_id"] == "SET NULL"
    # The table gets rebuilt, so prove the swap did not disturb the other two.
    assert after["from_id"] == "CASCADE"
    assert after["project_id"] == "NO ACTION"


def test_sqlite_upgrade_preserves_relation_rows_and_indexes(tmp_path, monkeypatch):
    """The SQLite copy-and-swap must not drop data, indexes, or unique constraints."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "relation-to-id-set-null-data.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, DOWN_REVISION)

    timestamp = "2026-08-28 00:00:00"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO project (
                id, name, permalink, path, is_active, is_default,
                created_at, updated_at, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "test", "test", "/test", True, True, timestamp, timestamp, "project-1"),
        )
        connection.executemany(
            """
            INSERT INTO entity (
                id, title, note_type, content_type, file_path,
                created_at, updated_at, project_id, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entity_id,
                    f"Note {entity_id}",
                    "note",
                    "text/markdown",
                    f"note-{entity_id}.md",
                    timestamp,
                    timestamp,
                    1,
                    f"entity-{entity_id}",
                )
                for entity_id in (1, 2)
            ],
        )
        connection.execute(
            """
            INSERT INTO relation (
                id, from_id, to_id, to_name, relation_type, project_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1, 1, 2, "Note 2", "links_to", 1),
        )
        connection.commit()
        indexes_before = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='relation'"
            )
        }
    finally:
        connection.close()

    command.upgrade(config, REVISION)

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM relation").fetchone()[0] == 1
        row = connection.execute(
            "SELECT from_id, to_id, to_name, relation_type, generation FROM relation"
        ).fetchone()
        assert row == (1, 2, "Note 2", "links_to", 0)

        indexes_after = {
            name
            for name in (
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='relation'"
                )
            )
        }
        assert indexes_before <= indexes_after

        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='relation'"
        ).fetchone()[0]
        assert "uix_relation_from_id_to_id" in table_sql
        assert "uix_relation_from_id_to_name" in table_sql

        # The whole point: the delete now unresolves the inbound row.
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM entity WHERE id = 2")
        connection.commit()
        assert connection.execute("SELECT to_id, to_name FROM relation").fetchone() == (
            None,
            "Note 2",
        )
    finally:
        connection.close()


def test_sqlite_downgrade_restores_cascade(tmp_path, monkeypatch):
    """Downgrading must put the old CASCADE back, so the step is reversible."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "relation-to-id-downgrade.db"
    config = sqlite_alembic_config(database_path)

    command.upgrade(config, REVISION)
    assert _relation_fk_actions(database_path)["to_id"] == "SET NULL"

    command.downgrade(config, DOWN_REVISION)
    restored = _relation_fk_actions(database_path)

    assert restored["to_id"] == "CASCADE"
    assert restored["from_id"] == "CASCADE"


# What op.drop_constraint / op.create_foreign_key get called with, recorded verbatim.
DropConstraintCall = tuple[str, str, str]
CreateForeignKeyCall = tuple[str, str, str, list[str], list[str], str]


def _postgres_bind() -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))


def _record_postgres_constraint_calls(
    monkeypatch,
) -> tuple[list[DropConstraintCall], list[CreateForeignKeyCall]]:
    """Swap op's constraint calls for recorders; Postgres alters the FK in place."""
    dropped: list[DropConstraintCall] = []
    created: list[CreateForeignKeyCall] = []
    monkeypatch.setattr(migration.op, "get_bind", _postgres_bind)
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, table, type_: dropped.append((name, table, type_)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_foreign_key",
        lambda name, source, referent, local_cols, remote_cols, ondelete: created.append(
            (name, source, referent, local_cols, remote_cols, ondelete)
        ),
    )
    return dropped, created


def test_postgres_upgrade_recreates_to_id_fk_with_set_null(monkeypatch):
    """Postgres has no batch dance; it drops and recreates the named constraint."""
    dropped, created = _record_postgres_constraint_calls(monkeypatch)

    migration.upgrade()

    assert dropped == [("relation_to_id_fkey", "relation", "foreignkey")]
    assert created == [("relation_to_id_fkey", "relation", "entity", ["to_id"], ["id"], "SET NULL")]


def test_postgres_downgrade_recreates_to_id_fk_with_cascade(monkeypatch):
    """And puts CASCADE back on the way down."""
    dropped, created = _record_postgres_constraint_calls(monkeypatch)

    migration.downgrade()

    assert dropped == [("relation_to_id_fkey", "relation", "foreignkey")]
    assert created == [("relation_to_id_fkey", "relation", "entity", ["to_id"], ["id"], "CASCADE")]
