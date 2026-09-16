"""End-to-end coverage for issue #539's SQLite half: BASIC_MEMORY_DATABASE_URL /
config ``database_url`` accepting a ``sqlite+aiosqlite://`` URL and the engine
actually reading/writing the file it points at (not the default
~/.basic-memory/memory.db).

Config-level URL parsing (relative/absolute, error cases) is covered in
tests/test_config.py::TestBasicMemoryConfig - this module proves the engine
layer honors config.app_database_path once it is derived from database_url,
including the WAL/pragma setup in db.py.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.db import DatabaseType, engine_session_factory


@pytest.mark.asyncio
async def test_engine_creates_sqlite_file_at_custom_relative_database_url(tmp_path, monkeypatch):
    """The engine writes to the custom path derived from a relative database_url."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)

    config = BasicMemoryConfig(
        env="test",
        database_backend=DatabaseBackend.SQLITE,
        database_url="sqlite+aiosqlite:///.basic-memory/memory.db",
        skip_initialization_sync=True,
    )

    db_path = config.app_database_path
    assert db_path == Path(".basic-memory/memory.db")

    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM, config) as (
        engine,
        session_maker,
    ):
        async with session_maker() as session:
            # Force a real connection/write so SQLite actually creates the file
            # and applies the WAL pragma from _configure_sqlite_connection.
            await session.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
            await session.execute(text("INSERT INTO probe (id) VALUES (1)"))
            await session.commit()

            result = await session.execute(text("PRAGMA journal_mode"))
            assert result.scalar() == "wal"

    resolved_db = tmp_path / ".basic-memory" / "memory.db"
    assert resolved_db.exists()
    assert resolved_db.stat().st_size > 0

    # The default per-user location must never have been created.
    assert not (tmp_path / ".basic-memory-home" / "memory.db").exists()


@pytest.mark.asyncio
async def test_engine_creates_sqlite_file_at_custom_absolute_database_url(tmp_path, monkeypatch):
    """The engine writes to the custom path derived from an absolute database_url."""
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    custom_db = tmp_path / "instance-a" / "index.db"

    config = BasicMemoryConfig(
        env="test",
        database_backend=DatabaseBackend.SQLITE,
        database_url=f"sqlite+aiosqlite:///{custom_db}",
        skip_initialization_sync=True,
    )

    db_path = config.app_database_path
    assert db_path == custom_db

    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM, config) as (
        engine,
        session_maker,
    ):
        async with session_maker() as session:
            await session.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
            await session.commit()

    assert custom_db.exists()


@pytest.mark.asyncio
async def test_engine_uses_default_path_when_database_url_unset(tmp_path, monkeypatch):
    """Unset database_url keeps the pre-existing default SQLite path unchanged."""
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "state"))

    config = BasicMemoryConfig(env="test", skip_initialization_sync=True)

    db_path = config.app_database_path
    assert db_path == tmp_path / "state" / "memory.db"

    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM, config) as (
        engine,
        session_maker,
    ):
        async with session_maker() as session:
            await session.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
            await session.commit()

    assert (tmp_path / "state" / "memory.db").exists()
