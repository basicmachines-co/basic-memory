"""Migration 0045 lets a note carry a frontmatter status longer than a btree entry.

Postgres btree entries are capped at 2704 bytes. The indexes on ``status`` and ``type``
that d7e8f9a0b1c2 created made a note with a multi-kilobyte ``status`` fail to insert,
so it never reached search. The upgrade drops them on Postgres; SQLite never had the
limit.
"""

import hashlib
import json
from importlib import import_module

from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.test_entity_file_path_conflict_keys_migration import (  # noqa: F401 (fixture)
    _alembic_config,
    migration_database,
)

migration = import_module("basic_memory.alembic.versions.0045_drop_frontmatter_btree_indexes")
PREVIOUS_REVISION = str(migration.down_revision)
FRONTMATTER_INDEXES = {"idx_entity_frontmatter_status", "idx_entity_frontmatter_type"}

# Postgres compresses index entries, so repetitive text slips under the 2704-byte limit.
# Hex digests do not compress, so this stays over it.
LONG_STATUS = " ".join(hashlib.sha256(str(n).encode()).hexdigest() for n in range(100))


def _insert_note(engine: Engine, metadata: dict[str, str]) -> None:
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
                "entity_metadata, created_at, updated_at) VALUES (1, 'long-status', 'Note', "
                "'note', 'text/markdown', 1, 'note.md', 'note.md', 'note', 'checksum', "
                ":metadata, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"metadata": json.dumps(metadata)},
        )


def _entity_indexes(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {str(index["name"]) for index in inspect(connection).get_indexes("entity")}


def test_upgrade_accepts_a_status_longer_than_a_btree_entry(
    migration_database: tuple[str, Engine],  # noqa: F811 (fixture)
) -> None:
    database_url, engine = migration_database
    command.upgrade(_alembic_config(database_url), migration.revision)

    assert len(LONG_STATUS.encode()) > 2704
    _insert_note(engine, {"status": LONG_STATUS, "type": LONG_STATUS})

    with engine.connect() as connection:
        stored = connection.execute(text("SELECT entity_metadata FROM entity")).scalar_one()
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored["status"] == LONG_STATUS


def test_upgrade_drops_the_indexes_on_postgres_only(
    migration_database: tuple[str, Engine],  # noqa: F811 (fixture)
) -> None:
    database_url, engine = migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    assert FRONTMATTER_INDEXES <= _entity_indexes(engine)

    command.upgrade(config, migration.revision)

    remaining = FRONTMATTER_INDEXES & _entity_indexes(engine)
    if engine.dialect.name == "postgresql":
        assert remaining == set()
    else:
        assert remaining == FRONTMATTER_INDEXES


def test_downgrade_restores_the_indexes(
    migration_database: tuple[str, Engine],  # noqa: F811 (fixture)
) -> None:
    database_url, engine = migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, migration.revision)
    command.downgrade(config, PREVIOUS_REVISION)

    assert FRONTMATTER_INDEXES <= _entity_indexes(engine)
