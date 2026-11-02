"""Migration 0043 backfills entity file path conflict keys on SQLite and Postgres."""

import uuid
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from basic_memory.utils import file_path_conflict_keys
from tests.test_note_content_migration import sqlite_alembic_config

migration = import_module("basic_memory.alembic.versions.0043_entity_file_path_conflict_keys")
PREVIOUS_REVISION = str(migration.down_revision)

# Paths that exercise both keys: case and separator variants, accents, CJK, a
# non-markdown file, and two paths that generate one permalink.
SEEDED_PATHS = [
    "notes/Foo Bar.md",
    "notes/foo_bar.md",
    "Notes/Café Menu.md",
    "notes/café menu.md",
    "docs\\Windows Path.md",
    "中文/测试文档.md",
    "files/My Image.png",
    "releases/v2.0.1 Notes.md",
]
# Enough extra rows that the backfill pages through several id-ordered batches.
SEEDED_PATHS += [
    f"bulk/Topic {index}/Note {index}.md" for index in range(2 * migration.BACKFILL_BATCH_SIZE + 5)
]


def _alembic_config(database_url: str) -> Config:
    """Alembic config for a throwaway database at database_url."""
    config = sqlite_alembic_config(Path("unused.db"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def migration_database(
    db_backend: str, postgres_container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, Engine]]:
    """Yield an empty database for this run's backend, as (alembic url, sync engine)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    if db_backend != "postgres":
        database_path = tmp_path / "conflict-keys.db"
        engine = create_engine(f"sqlite:///{database_path}")
        try:
            yield f"sqlite:///{database_path}", engine
        finally:
            engine.dispose()
        return

    from tests.conftest import _resolve_postgres_sync_url

    # The shared test URL names psycopg2, which is not installed; psycopg (v3) is.
    server_url = _resolve_postgres_sync_url(postgres_container).replace(
        "postgresql+psycopg2", "postgresql+psycopg"
    )
    database_name = f"conflict_keys_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {database_name}"))
    sync_url = server_url.rsplit("/", 1)[0] + f"/{database_name}"
    engine = create_engine(sync_url)
    try:
        yield sync_url.replace("postgresql+psycopg", "postgresql+asyncpg"), engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {database_name} WITH (FORCE)"))
        admin.dispose()


def _seed(engine: Engine, paths: list[str]) -> None:
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
                "project_id, permalink, file_path, created_at, updated_at) VALUES (:id, "
                ":external_id, 'Note', 'note', :content_type, 1, :permalink, :file_path, "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            [
                {
                    "id": index,
                    "external_id": f"entity-{index}",
                    "content_type": "text/markdown" if path.endswith(".md") else "image/png",
                    # Stored permalinks are unique per project and play no part in the
                    # keys, so each row gets its own.
                    "permalink": f"main/stored-{index}" if path.endswith(".md") else None,
                    "file_path": path,
                }
                for index, path in enumerate(paths, start=1)
            ],
        )


def test_upgrade_backfills_conflict_keys_for_existing_rows(
    migration_database: tuple[str, Engine],
) -> None:
    database_url, engine = migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed(engine, SEEDED_PATHS)

    command.upgrade(config, migration.revision)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT file_path, file_path_key, file_path_permalink FROM entity ORDER BY id")
        ).all()
        columns = {column["name"]: column for column in inspect(connection).get_columns("entity")}
        indexes = {
            index["name"]: index["column_names"]
            for index in inspect(connection).get_indexes("entity")
        }

    assert [row[0] for row in rows] == SEEDED_PATHS
    for file_path, path_key, permalink in rows:
        keys = file_path_conflict_keys(file_path)
        assert (path_key, permalink) == (keys.path_key, keys.permalink), file_path
    assert not columns["file_path_key"]["nullable"]
    assert not columns["file_path_permalink"]["nullable"]
    assert indexes["ix_entity_project_file_path_key"] == ["project_id", "file_path_key"]
    assert indexes["ix_entity_project_file_path_permalink"] == [
        "project_id",
        "file_path_permalink",
    ]


def test_downgrade_drops_conflict_keys(migration_database: tuple[str, Engine]) -> None:
    database_url, engine = migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, migration.revision)
    command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        columns = [column["name"] for column in inspect(connection).get_columns("entity")]
        indexes = [index["name"] for index in inspect(connection).get_indexes("entity")]

    assert "file_path_key" not in columns
    assert "file_path_permalink" not in columns
    assert len(columns) == len(set(columns)), f"entity has duplicated columns: {columns}"
    assert "ix_entity_project_file_path_key" not in indexes
    assert "ix_entity_project_file_path_permalink" not in indexes
