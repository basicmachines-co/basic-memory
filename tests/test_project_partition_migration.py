"""Migration regressions for accepted project change storage."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from basic_memory import db


def _sqlite_alembic_config(database_path: Path) -> Config:
    alembic_dir = Path(db.__file__).parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option("revision_environment", "false")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_upgrade_repairs_stamped_project_partition_without_change_table(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A tenant stamped at the pre-release revision receives its missing journal."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "project-partition-repair.db"
    config = _sqlite_alembic_config(database_path)

    command.upgrade(config, "bcdbd5a942ca")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "ALTER TABLE project ADD COLUMN partition_position INTEGER NOT NULL DEFAULT 0"
        )
        connection.commit()
    finally:
        connection.close()
    command.stamp(config, "s2p3e4c5w6k7")

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(accepted_project_note_change)"
            ).fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(accepted_project_note_change)"
            ).fetchall()
        }
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()

    assert columns == {
        "id",
        "project_id",
        "project_external_id",
        "partition_position",
        "entity_id",
        "note_external_id",
        "permalink",
        "title",
        "operation",
        "file_path",
        "previous_file_path",
        "accepted_at",
        "source",
        "db_version",
        "db_checksum",
        "actor_user_profile_id",
        "actor_kind",
        "actor_name",
        "materialized_at",
    }
    assert "partition_position" in project_columns
    assert "ix_accepted_project_note_change_project_materialized" in indexes
    assert "ix_accepted_project_note_change_note_external_id" in indexes
    assert version == (ScriptDirectory.from_config(config).get_current_head(),)


def test_upgrade_backfills_permalink_in_pre_release_change_table(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A pre-release journal keeps a stable identity when permalink was absent."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    database_path = tmp_path / "project-partition-permalink-repair.db"
    config = _sqlite_alembic_config(database_path)

    command.upgrade(config, "bcdbd5a942ca")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "ALTER TABLE project ADD COLUMN partition_position INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute(
            """
            INSERT INTO project (
                id,
                external_id,
                name,
                permalink,
                path,
                is_active,
                created_at,
                updated_at,
                partition_position
            ) VALUES (
                1,
                'project-1',
                'Project 1',
                'project-1',
                '/project-1',
                1,
                '2026-08-29 12:00:00',
                '2026-08-29 12:00:00',
                0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE accepted_project_note_change (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                project_external_id TEXT NOT NULL,
                partition_position INTEGER NOT NULL,
                entity_id INTEGER NOT NULL,
                note_external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                operation TEXT NOT NULL,
                file_path TEXT NOT NULL,
                previous_file_path TEXT,
                accepted_at DATETIME NOT NULL,
                source TEXT NOT NULL,
                db_version INTEGER,
                db_checksum TEXT,
                actor_user_profile_id TEXT,
                actor_kind TEXT,
                actor_name TEXT,
                materialized_at DATETIME
            )
            """
        )
        connection.execute(
            """
            INSERT INTO accepted_project_note_change (
                project_id,
                project_external_id,
                partition_position,
                entity_id,
                note_external_id,
                title,
                operation,
                file_path,
                accepted_at,
                source
            ) VALUES (1, 'project-1', 1, 999, 'note-legacy', 'Legacy',
                      'deleted', 'legacy.md', '2026-08-29 12:00:00', 'api')
            """
        )
        connection.commit()
    finally:
        connection.close()
    command.stamp(config, "s2p3e4c5w6k7")

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        column = next(
            row
            for row in connection.execute(
                "PRAGMA table_info(accepted_project_note_change)"
            ).fetchall()
            if row[1] == "permalink"
        )
        permalink = connection.execute(
            "SELECT permalink FROM accepted_project_note_change WHERE id = 1"
        ).fetchone()
        partition_position = connection.execute(
            "SELECT partition_position FROM project WHERE id = 1"
        ).fetchone()
        [next_partition_position] = connection.execute(
            """
            UPDATE project
            SET partition_position = partition_position + 1
            WHERE id = 1
            RETURNING partition_position
            """
        ).fetchone()
    finally:
        connection.close()

    assert column[3] == 1
    assert permalink == ("note-legacy",)
    assert partition_position == (1,)
    assert next_partition_position == 2
