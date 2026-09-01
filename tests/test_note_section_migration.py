"""Migration tests for the note_section table (SPEC-47 / #1403)."""

import sqlite3

from alembic import command

from basic_memory.alembic.versions import (  # type: ignore[attr-defined]
    t3n4o5t6e7s8_add_note_section_table as migration,
)
from tests.test_note_content_migration import sqlite_alembic_config

# Pin the downgrade target to this migration's own parent. A relative "-1" would
# instead undo whichever migration currently sits at head, so the test would break
# every time a later revision lands.
DOWN_REVISION: str = str(migration.down_revision)


def test_alembic_upgrade_creates_note_section_table(tmp_path, monkeypatch):
    """Running Alembic head creates note_section with its expected contract."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "note-section-migration.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(note_section)").fetchall()
        }
        assert columns == {
            "id",
            "project_id",
            "entity_id",
            "heading",
            "level",
            "heading_path",
            "heading_path_digest",
            "duplicate_index",
            "start_line",
            "end_line",
            "start_offset",
            "end_offset",
        }

        foreign_keys = connection.execute("PRAGMA foreign_key_list(note_section)").fetchall()
        entity_fk = next(row for row in foreign_keys if row[3] == "entity_id")
        project_fk = next(row for row in foreign_keys if row[3] == "project_id")
        assert entity_fk[2] == "entity"
        assert entity_fk[4] == "id"
        # Sections are removed with their entity at the database level.
        assert entity_fk[6].upper() == "CASCADE"
        assert project_fk[2] == "project"
        assert project_fk[4] == "id"

        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(note_section)").fetchall()
        }
        assert "ix_note_section_project_id" in indexes
        assert "ix_note_section_entity_id" in indexes
        assert "ix_note_section_entity_path" in indexes
        lookup_columns = [
            row[2]
            for row in connection.execute(
                "PRAGMA index_info(ix_note_section_entity_path)"
            ).fetchall()
        ]
        assert lookup_columns == ["entity_id", "heading_path_digest", "duplicate_index"]

        duplicate_default = next(
            row
            for row in connection.execute("PRAGMA table_info(note_section)").fetchall()
            if row[1] == "duplicate_index"
        )
        assert duplicate_default[4] == "0"
    finally:
        connection.close()


def test_alembic_downgrade_drops_note_section_table(tmp_path, monkeypatch):
    """Downgrading past this revision removes the table and its indexes."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "note-section-downgrade.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, DOWN_REVISION)

    connection = sqlite3.connect(database_path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'note_section'"
        ).fetchone()
    finally:
        connection.close()

    assert table_exists is None
