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


def test_relation_generation_migration_backfills_source_db_version(tmp_path, monkeypatch):
    """Existing relations inherit their source note generation during upgrade."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "relation-generation-migration.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "q0l1m2n3o4p5")

    connection = sqlite3.connect(database_path)
    try:
        timestamp = "2026-08-09 00:00:00"
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
                    f"Source {entity_id}",
                    "note",
                    "text/markdown",
                    f"source-{entity_id}.md",
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
            INSERT INTO note_content (
                entity_id, project_id, external_id, file_path, markdown_content,
                db_version, db_checksum, file_write_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                1,
                "content-1",
                "source-1.md",
                "# Source 1",
                17,
                "checksum",
                "synced",
                timestamp,
            ),
        )
        connection.executemany(
            """
            INSERT INTO relation (
                id, from_id, to_name, relation_type, project_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "Target A", "links_to", 1),
                (2, 2, "Target B", "links_to", 1),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        generation_column = next(
            row
            for row in connection.execute("PRAGMA table_info(relation)")
            if row[1] == "generation"
        )
        generations = connection.execute(
            "SELECT id, generation FROM relation ORDER BY id"
        ).fetchall()
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(relation)")}
        refresh_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(relation_search_refresh)")
        }
        refresh_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(relation_search_refresh)")
        }
    finally:
        connection.close()

    assert generation_column[2].upper() == "BIGINT"
    assert generation_column[3] == 1
    assert generation_column[4] == "0"
    assert generations == [(1, 17), (2, 0)]
    assert "ix_relation_project_from_generation" in indexes
    assert refresh_columns["publication_generation"][2].upper() == "BIGINT"
    assert refresh_columns["publication_generation"][3] == 0
    assert "ix_relation_search_refresh_project_publication_generation" in refresh_indexes


def _seed_duplicate_observations(
    connection: sqlite3.Connection,
    *,
    create_search_index: bool,
) -> None:
    timestamp = "2026-08-10 00:00:00"
    connection.execute(
        """
        INSERT INTO project (
            id, name, permalink, path, is_active, is_default,
            created_at, updated_at, external_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "test", "test", "/test", True, True, timestamp, timestamp, "project-1"),
    )
    connection.execute(
        """
        INSERT INTO entity (
            id, title, note_type, content_type, file_path,
            created_at, updated_at, project_id, external_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Source",
            "note",
            "text/markdown",
            "source.md",
            timestamp,
            timestamp,
            1,
            "entity-1",
        ),
    )
    connection.executemany(
        """
        INSERT INTO observation (
            id, entity_id, category, content, context, tags, project_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (10, 1, "fact", "Duplicate", None, '["migration"]', 1),
            (11, 1, "fact", "Duplicate", None, '["migration"]', 1),
            (12, 1, "fact", "Distinct", "context", None, 1),
        ],
    )
    if create_search_index:
        connection.execute("CREATE TABLE search_index (id INTEGER, type TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO search_index (id, type) VALUES (?, ?)",
            [
                (10, "observation"),
                (11, "observation"),
                (12, "observation"),
                (999, "observation"),
                (999, "entity"),
            ],
        )
    connection.commit()


def test_observation_repair_migration_dedupes_and_purges_search_orphans(
    tmp_path,
    monkeypatch,
) -> None:
    """The data repair keeps MIN(id) and removes only orphaned observation search rows."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "observation-repair-migration.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "r1m2n3o4p5q6")
    connection = sqlite3.connect(database_path)
    try:
        _seed_duplicate_observations(connection, create_search_index=True)
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        observations = connection.execute(
            "SELECT id, content FROM observation ORDER BY id"
        ).fetchall()
        search_rows = connection.execute(
            "SELECT id, type FROM search_index ORDER BY type, id"
        ).fetchall()
    finally:
        connection.close()

    assert observations == [(10, "Duplicate"), (12, "Distinct")]
    assert search_rows == [
        (999, "entity"),
        (10, "observation"),
        (12, "observation"),
    ]


def test_observation_repair_migration_skips_missing_search_index(
    tmp_path,
    monkeypatch,
) -> None:
    """Fresh SQLite installs without the runtime FTS table still complete the repair."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "observation-repair-without-search.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "r1m2n3o4p5q6")
    connection = sqlite3.connect(database_path)
    try:
        _seed_duplicate_observations(connection, create_search_index=False)
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        observation_ids = [
            row[0] for row in connection.execute("SELECT id FROM observation ORDER BY id")
        ]
        search_index_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'search_index'"
        ).fetchone()
    finally:
        connection.close()

    assert observation_ids == [10, 12]
    assert search_index_exists is None
