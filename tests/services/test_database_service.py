"""Tests for DatabaseService."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import Column, String, Table, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

from basic_memory import db
from basic_memory.config import ProjectConfig
from basic_memory.db import DatabaseType
from basic_memory.models import Base
from basic_memory.services.database_service import DatabaseService
from basic_memory.sync import SyncService


@pytest_asyncio.fixture(scope="function")
async def engine_factory(
    test_config,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Special version of the engine factory fixture that uses a FILESYSTEM db_type"""
    async with db.engine_session_factory(
        db_path=test_config.database_path, db_type=DatabaseType.FILESYSTEM
    ) as (engine, session_maker):
        # Initialize database
        async with db.scoped_session(session_maker) as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            conn = await session.connection()
            await conn.run_sync(Base.metadata.create_all)

        yield engine, session_maker


@pytest_asyncio.fixture
async def database_service(
    test_config: ProjectConfig,
    sync_service: SyncService,
) -> DatabaseService:
    """Create DatabaseManagementService instance for testing."""
    return DatabaseService(config=test_config, db_type=DatabaseType.FILESYSTEM)


@pytest.mark.asyncio
async def test_check_db_initializes_new_db(
    database_service: DatabaseService,
):
    """Test that check_db initializes new database."""
    # Ensure DB doesn't exist
    if Path(database_service.db_path).exists():
        Path(database_service.db_path).unlink()

    # Check DB - should initialize
    assert await database_service.check_db()


@pytest.mark.asyncio
async def test_check_db_rebuilds_on_schema_mismatch(
    database_service: DatabaseService,
    session_maker,
):
    """Test that check_db rebuilds DB when schema doesn't match."""
    # Initialize DB first
    assert await database_service.check_db()

    # Alter an existing table to remove a column
    async with db.scoped_session(session_maker) as session:
        conn = await session.connection()
        # Create temp table
        await conn.execute(
            text("""
            CREATE TABLE entity_temp (
                id INTEGER PRIMARY KEY,
                title TEXT,
                entity_type TEXT,
                content_type TEXT,
                permalink TEXT,
                file_path TEXT,
                checksum TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
                -- Deliberately omit entity_metadata column
            )
        """)
        )
        # Drop original table
        await conn.execute(text("DROP TABLE entity"))
        # Rename temp table
        await conn.execute(text("ALTER TABLE entity_temp RENAME TO entity"))
        await session.commit()

    # Check DB - should detect missing column and rebuild
    assert await database_service.check_db()

    # Verify entity_metadata column exists now
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text("""
            SELECT sql FROM sqlite_master 
            WHERE type='table' AND name='entity'
        """)
        )
        create_sql = result.scalar()
        assert "entity_metadata" in create_sql.lower()


@pytest.mark.asyncio
async def test_backup_creates_timestamped_file(
    database_service: DatabaseService,
):
    """Test that backup creates properly named backup file."""
    if database_service.db_type == db.DatabaseType.MEMORY:
        return

    # Create dummy DB file
    database_service.db_path.parent.mkdir(parents=True, exist_ok=True)
    database_service.db_path.write_text("test content")

    # Create backup
    backup_path = await database_service.create_backup()

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.suffix == ".backup"
    assert datetime.now().strftime("%Y%m%d") in backup_path.name


@pytest.mark.asyncio
async def test_cleanup_backups_keeps_recent(
    database_service: DatabaseService,
):
    """Test that cleanup_backups keeps N most recent backups."""
    if database_service.db_type == db.DatabaseType.MEMORY:
        return

    # Create backup directory
    backup_dir = database_service.db_path.parent
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Create some test backup files with different timestamps
    backup_times = [
        datetime.now() - timedelta(days=i)
        for i in range(7)  # Create 7 backups
    ]

    for dt in backup_times:
        timestamp = dt.strftime("%Y%m%d_%H%M%S")
        backup_path = database_service.db_path.with_suffix(f".{timestamp}.backup")
        backup_path.write_text("test backup")
        # Set mtime to match our timestamp
        backup_path.touch()
        ts = dt.timestamp()

    # Clean up keeping 5 most recent
    await database_service.cleanup_backups(keep_count=5)

    # Check that we have exactly 5 backups left
    backup_pattern = "*.backup"
    remaining = list(backup_dir.glob(backup_pattern))
    assert len(remaining) == 5
