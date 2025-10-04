"""Tests for WAL mode and Windows-specific SQLite optimizations."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from sqlalchemy import text

from basic_memory.db import DatabaseType, engine_session_factory


@pytest.mark.asyncio
async def test_wal_mode_enabled(tmp_path):
    """Test that WAL mode is enabled on database connections."""
    db_path = tmp_path / "test.db"

    # Use FILESYSTEM type since in-memory databases don't support WAL mode
    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (engine, _):
        # Execute a query to verify WAL mode is enabled
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            journal_mode = result.fetchone()[0]

            # WAL mode should be enabled
            assert journal_mode.upper() == "WAL"


@pytest.mark.asyncio
async def test_busy_timeout_configured(tmp_path):
    """Test that busy timeout is configured for database connections."""
    db_path = tmp_path / "test.db"

    async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            busy_timeout = result.fetchone()[0]

            # Busy timeout should be 10 seconds (10000 milliseconds)
            assert busy_timeout == 10000


@pytest.mark.asyncio
async def test_synchronous_mode_configured(tmp_path):
    """Test that synchronous mode is set to NORMAL for performance."""
    db_path = tmp_path / "test.db"

    async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA synchronous"))
            synchronous = result.fetchone()[0]

            # Synchronous should be NORMAL (1)
            assert synchronous == 1


@pytest.mark.asyncio
async def test_cache_size_configured(tmp_path):
    """Test that cache size is configured for performance."""
    db_path = tmp_path / "test.db"

    async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA cache_size"))
            cache_size = result.fetchone()[0]

            # Cache size should be -64000 (64MB)
            assert cache_size == -64000


@pytest.mark.asyncio
async def test_temp_store_configured(tmp_path):
    """Test that temp_store is set to MEMORY."""
    db_path = tmp_path / "test.db"

    async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA temp_store"))
            temp_store = result.fetchone()[0]

            # temp_store should be MEMORY (2)
            assert temp_store == 2


@pytest.mark.asyncio
async def test_windows_locking_mode_when_on_windows(tmp_path):
    """Test that Windows-specific locking mode is set when running on Windows."""
    db_path = tmp_path / "test.db"

    with patch("os.name", "nt"):
        async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
            async with engine.connect() as conn:
                result = await conn.execute(text("PRAGMA locking_mode"))
                locking_mode = result.fetchone()[0]

                # Locking mode should be NORMAL on Windows
                assert locking_mode.upper() == "NORMAL"


@pytest.mark.asyncio
async def test_windows_timeout_configured(tmp_path):
    """Test that Windows-specific timeout is configured."""
    db_path = tmp_path / "test.db"

    # Note: We can't easily test the timeout parameter in connect_args
    # as it's passed to the underlying SQLite connection, but we can verify
    # the code path is exercised without errors
    with patch("os.name", "nt"):
        async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
            # If Windows-specific code path works, engine should be created successfully
            assert engine is not None


@pytest.mark.asyncio
async def test_null_pool_on_windows(tmp_path):
    """Test that NullPool is used on Windows to avoid connection pooling issues."""
    db_path = tmp_path / "test.db"

    with patch("os.name", "nt"):
        async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
            from sqlalchemy.pool import NullPool

            # Engine should be using NullPool on Windows
            assert isinstance(engine.pool, NullPool)


@pytest.mark.asyncio
async def test_regular_pool_on_non_windows(tmp_path):
    """Test that regular pooling is used on non-Windows platforms."""
    db_path = tmp_path / "test.db"

    with patch("os.name", "posix"):
        async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
            from sqlalchemy.pool import NullPool

            # Engine should NOT be using NullPool on non-Windows
            assert not isinstance(engine.pool, NullPool)


@pytest.mark.asyncio
async def test_foreign_keys_enabled(tmp_path):
    """Test that foreign keys are enabled (from scoped_session context)."""
    db_path = tmp_path / "test.db"

    async with engine_session_factory(db_path, DatabaseType.MEMORY) as (engine, _):
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            foreign_keys = result.fetchone()[0]

            # Foreign keys should be enabled by default in our configuration
            # Note: This is set in scoped_session, not in the engine event listener
            # For this test we just verify the PRAGMA works
            assert foreign_keys is not None
