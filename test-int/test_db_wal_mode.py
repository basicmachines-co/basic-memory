"""Integration tests for WAL mode and Windows-specific SQLite optimizations.

These tests use real filesystem databases (not in-memory) to verify WAL mode
and other SQLite configuration settings work correctly in production scenarios.
"""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_wal_mode_enabled(engine_factory, db_backend):
    """Test that WAL mode is enabled on filesystem database connections."""
    if db_backend == "postgres":
        pytest.skip("SQLite-specific test - PRAGMA commands not supported in Postgres")

    engine, _ = engine_factory

    # Execute a query to verify WAL mode is enabled
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        journal_mode = result.fetchone()[0]

        # WAL mode should be enabled for filesystem databases
        assert journal_mode.upper() == "WAL"


@pytest.mark.asyncio
async def test_busy_timeout_configured(engine_factory, db_backend):
    """Test that busy timeout is configured for database connections."""
    if db_backend == "postgres":
        pytest.skip("SQLite-specific test - PRAGMA commands not supported in Postgres")

    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA busy_timeout"))
        busy_timeout = result.fetchone()[0]

        # Busy timeout should be 10 seconds (10000 milliseconds)
        assert busy_timeout == 10000


@pytest.mark.asyncio
async def test_synchronous_mode_configured(engine_factory, db_backend):
    """Test that synchronous mode is set to NORMAL for performance."""
    if db_backend == "postgres":
        pytest.skip("SQLite-specific test - PRAGMA commands not supported in Postgres")

    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA synchronous"))
        synchronous = result.fetchone()[0]

        # Synchronous should be NORMAL (1)
        assert synchronous == 1


@pytest.mark.asyncio
async def test_cache_size_configured(engine_factory, db_backend):
    """Test that cache size is configured for performance."""
    if db_backend == "postgres":
        pytest.skip("SQLite-specific test - PRAGMA commands not supported in Postgres")

    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA cache_size"))
        cache_size = result.fetchone()[0]

        # Cache size should be -64000 (64MB)
        assert cache_size == -64000


@pytest.mark.asyncio
async def test_temp_store_configured(engine_factory, db_backend):
    """Test that temp_store is set to MEMORY."""
    if db_backend == "postgres":
        pytest.skip("SQLite-specific test - PRAGMA commands not supported in Postgres")

    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA temp_store"))
        temp_store = result.fetchone()[0]

        # temp_store should be MEMORY (2)
        assert temp_store == 2


@pytest.mark.asyncio
@pytest.mark.windows
@pytest.mark.skipif(
    __import__("os").name != "nt", reason="Windows-specific test - only runs on Windows platform"
)
async def test_windows_locking_mode_when_on_windows(tmp_path, monkeypatch, config_manager):
    """Test that Windows-specific locking mode is set when running on Windows."""
    from basic_memory.db import engine_session_factory, DatabaseType
    from basic_memory.config import DatabaseBackend

    # Force SQLite backend for this SQLite-specific test
    config_manager.config.database_backend = DatabaseBackend.SQLITE

    # Set HOME environment variable
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    db_path = tmp_path / "test_windows.db"

    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (
        engine,
        _,
    ):
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA locking_mode"))
            locking_mode = result.fetchone()[0]

            # Locking mode should be NORMAL on Windows
            assert locking_mode.upper() == "NORMAL"


@pytest.mark.asyncio
@pytest.mark.windows
@pytest.mark.skipif(
    __import__("os").name != "nt", reason="Windows-specific test - only runs on Windows platform"
)
async def test_null_pool_on_windows(tmp_path, monkeypatch):
    """Test that NullPool is used on Windows to avoid connection pooling issues."""
    from basic_memory.db import engine_session_factory, DatabaseType
    from sqlalchemy.pool import NullPool

    # Set HOME environment variable
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    db_path = tmp_path / "test_windows_pool.db"

    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (engine, _):
        # Engine should be using NullPool on Windows
        assert isinstance(engine.pool, NullPool)


@pytest.mark.asyncio
@pytest.mark.windows
@pytest.mark.skipif(
    __import__("os").name != "nt", reason="Windows-specific test - only runs on Windows platform"
)
async def test_memory_database_no_null_pool_on_windows(tmp_path, monkeypatch):
    """Test that in-memory databases do NOT use NullPool even on Windows.

    NullPool closes connections immediately, which destroys in-memory databases.
    This test ensures in-memory databases maintain connection pooling.
    """
    from basic_memory.db import engine_session_factory, DatabaseType
    from sqlalchemy.pool import NullPool

    # Set HOME environment variable
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    db_path = tmp_path / "test_memory.db"

    async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
        # In-memory databases should NOT use NullPool on Windows
        assert not isinstance(engine.pool, NullPool)
