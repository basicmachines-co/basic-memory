"""Integration tests for WAL mode and Windows-specific SQLite optimizations.

These tests use real filesystem databases (not in-memory) to verify WAL mode
and other SQLite configuration settings work correctly in production scenarios.
"""

import pytest
from unittest.mock import patch
from sqlalchemy import text


@pytest.mark.asyncio
async def test_wal_mode_enabled(engine_factory):
    """Test that WAL mode is enabled on filesystem database connections."""
    engine, _ = engine_factory

    # Execute a query to verify WAL mode is enabled
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        journal_mode = result.fetchone()[0]

        # WAL mode should be enabled for filesystem databases
        assert journal_mode.upper() == "WAL"


@pytest.mark.asyncio
async def test_busy_timeout_configured(engine_factory):
    """Test that busy timeout is configured for database connections."""
    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA busy_timeout"))
        busy_timeout = result.fetchone()[0]

        # Busy timeout should be 10 seconds (10000 milliseconds)
        assert busy_timeout == 10000


@pytest.mark.asyncio
async def test_synchronous_mode_configured(engine_factory):
    """Test that synchronous mode is set to NORMAL for performance."""
    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA synchronous"))
        synchronous = result.fetchone()[0]

        # Synchronous should be NORMAL (1)
        assert synchronous == 1


@pytest.mark.asyncio
async def test_cache_size_configured(engine_factory):
    """Test that cache size is configured for performance."""
    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA cache_size"))
        cache_size = result.fetchone()[0]

        # Cache size should be -64000 (64MB)
        assert cache_size == -64000


@pytest.mark.asyncio
async def test_temp_store_configured(engine_factory):
    """Test that temp_store is set to MEMORY."""
    engine, _ = engine_factory

    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA temp_store"))
        temp_store = result.fetchone()[0]

        # temp_store should be MEMORY (2)
        assert temp_store == 2


@pytest.mark.asyncio
async def test_windows_locking_mode_when_on_windows(tmp_path):
    """Test that Windows-specific locking mode is set when running on Windows."""
    from basic_memory.db import engine_session_factory, DatabaseType

    db_path = tmp_path / "test_windows.db"

    with patch("os.name", "nt"):
        # Need to patch at module level where it's imported
        with patch("basic_memory.db.os.name", "nt"):
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
async def test_null_pool_on_windows(tmp_path):
    """Test that NullPool is used on Windows to avoid connection pooling issues."""
    from basic_memory.db import engine_session_factory, DatabaseType
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_windows_pool.db"

    with patch("basic_memory.db.os.name", "nt"):
        async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (engine, _):
            # Engine should be using NullPool on Windows
            assert isinstance(engine.pool, NullPool)


@pytest.mark.asyncio
async def test_regular_pool_on_non_windows(tmp_path):
    """Test that regular pooling is used on non-Windows platforms."""
    from basic_memory.db import engine_session_factory, DatabaseType
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_posix_pool.db"

    with patch("basic_memory.db.os.name", "posix"):
        async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (engine, _):
            # Engine should NOT be using NullPool on non-Windows
            assert not isinstance(engine.pool, NullPool)


@pytest.mark.asyncio
async def test_memory_database_no_null_pool_on_windows(tmp_path):
    """Test that in-memory databases do NOT use NullPool even on Windows.

    NullPool closes connections immediately, which destroys in-memory databases.
    This test ensures in-memory databases maintain connection pooling.
    """
    from basic_memory.db import engine_session_factory, DatabaseType
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_memory.db"

    with patch("basic_memory.db.os.name", "nt"):
        async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
            # In-memory databases should NOT use NullPool on Windows
            assert not isinstance(engine.pool, NullPool)
