"""Migration tests for note_content schema."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from basic_memory import db


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


def test_alembic_upgrade_creates_note_content_table(tmp_path, monkeypatch):
    """Running Alembic head should create note_content with its expected contract."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "note-content-migration.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(note_content)").fetchall()
        }
        assert columns == {
            "entity_id",
            "project_id",
            "external_id",
            "file_path",
            "markdown_content",
            "db_version",
            "db_checksum",
            "file_version",
            "file_checksum",
            "file_write_status",
            "last_source",
            "updated_at",
            "file_updated_at",
            "last_materialization_error",
            "last_materialization_attempt_at",
        }

        foreign_keys = connection.execute("PRAGMA foreign_key_list(note_content)").fetchall()
        entity_fk = next(row for row in foreign_keys if row[3] == "entity_id")
        project_fk = next(row for row in foreign_keys if row[3] == "project_id")
        assert entity_fk[2] == "entity"
        assert entity_fk[4] == "id"
        assert entity_fk[6].upper() == "CASCADE"
        assert project_fk[2] == "project"
        assert project_fk[4] == "id"
        assert project_fk[6].upper() == "CASCADE"

        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(note_content)").fetchall()
        }
        assert "ix_note_content_project_id" in indexes
        assert "ix_note_content_file_path" in indexes
        assert "ix_note_content_external_id" in indexes
    finally:
        connection.close()
