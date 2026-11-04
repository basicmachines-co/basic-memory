"""Migration 0044 adds entity.embedded_checksum, backfills it, and indexes lagging notes."""

from importlib import import_module

from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from basic_memory.models.knowledge import ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE
from tests.test_entity_file_path_conflict_keys_migration import (  # noqa: F401 (fixture)
    _alembic_config,
    migration_database,
)

migration = import_module("basic_memory.alembic.versions.0044_entity_embedded_checksum")
PREVIOUS_REVISION = str(migration.down_revision)
INDEX_NAME = "ix_entity_project_embedding_not_current"


def _seed(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project (id, external_id, name, permalink, path, is_active, "
                "created_at, updated_at) VALUES (1, 'project-1', 'main', 'main', '/tmp/main', "
                "true, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO entity (id, external_id, title, note_type, content_type, "
                "project_id, file_path, file_path_key, file_path_permalink, checksum, "
                "vector_sync_deferred_at, created_at, updated_at) VALUES (:id, :external_id, "
                "'Note', 'note', 'text/markdown', 1, :file_path, :file_path, :file_path, "
                ":checksum, :deferred_at, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            [
                {
                    "id": 1,
                    "external_id": "settled",
                    "file_path": "settled.md",
                    "checksum": "settled-checksum",
                    "deferred_at": None,
                },
                {
                    "id": 2,
                    "external_id": "part-way",
                    "file_path": "part-way.md",
                    "checksum": "part-way-checksum",
                    "deferred_at": "2026-01-02 00:00:00",
                },
            ],
        )


def test_upgrade_backfills_and_indexes_lagging_notes(
    migration_database: tuple[str, Engine],  # noqa: F811 (fixture)
) -> None:
    database_url, engine = migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed(engine)

    command.upgrade(config, migration.revision)

    with engine.connect() as connection:
        stamps = {
            row[0]: row[1]
            for row in connection.execute(text("SELECT id, embedded_checksum FROM entity"))
        }
        behind = connection.execute(
            text(f"SELECT id FROM entity WHERE {ENTITY_EMBEDDING_NOT_CURRENT_PREDICATE}")
        ).all()
        indexes = {
            index["name"]: index["column_names"]
            for index in inspect(connection).get_indexes("entity")
        }

    # A settled note is taken as current; one a pass left part-way still owes work.
    assert stamps == {1: "settled-checksum", 2: None}
    assert [row[0] for row in behind] == [2]
    assert indexes[INDEX_NAME] == ["project_id", "updated_at"]


def test_downgrade_drops_the_column_and_index(
    migration_database: tuple[str, Engine],  # noqa: F811 (fixture)
) -> None:
    database_url, engine = migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, migration.revision)
    command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        columns = [column["name"] for column in inspect(connection).get_columns("entity")]
        indexes = [index["name"] for index in inspect(connection).get_indexes("entity")]

    assert "embedded_checksum" not in columns
    assert len(columns) == len(set(columns)), f"entity has duplicated columns: {columns}"
    assert INDEX_NAME not in indexes
