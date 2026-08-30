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
