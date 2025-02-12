"""Service for managing database lifecycle and schema validation."""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

from alembic.runtime.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import ProjectConfig
from basic_memory.models import Base


async def check_schema_matches_models(session: AsyncSession) -> Tuple[bool, List[str]]:
    """Check if database schema matches SQLAlchemy models.

    Returns:
        tuple[bool, list[str]]: (matches, list of differences)
    """
    # Get current DB schema via migration context
    conn = await session.connection()

    def _compare_schemas(connection):
        context = MigrationContext.configure(connection)
        return compare_metadata(context, Base.metadata)

    # Run comparison in sync context
    differences = await conn.run_sync(_compare_schemas)

    if not differences:
        return True, []

    # Format differences into readable messages
    diff_messages = []
    for diff in differences:
        if diff[0] == "add_table":
            diff_messages.append(f"Missing table: {diff[1].name}")
        elif diff[0] == "remove_table":
            diff_messages.append(f"Extra table: {diff[1].name}")
        elif diff[0] == "add_column":
            diff_messages.append(f"Missing column: {diff[3]} in table {diff[2]}")
        elif diff[0] == "remove_column":
            diff_messages.append(f"Extra column: {diff[3]} in table {diff[2]}")
        elif diff[0] == "modify_type":
            diff_messages.append(f"Column type mismatch: {diff[3]} in table {diff[2]}")

    return False, diff_messages


class DatabaseService:
    """Manages database lifecycle including schema validation and backups."""

    def __init__(
        self,
        config: ProjectConfig,
        db_type: db.DatabaseType = db.DatabaseType.FILESYSTEM,
    ):
        self.config = config
        self.db_path = Path(config.database_path)
        self.db_type = db_type

    async def create_backup(self) -> Optional[Path]:
        """Create backup of existing database file.

        Returns:
            Optional[Path]: Path to backup file if created, None if no DB exists
        """
        if not self.db_path.exists():
            return None

        # Create backup with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_path.with_suffix(f".{timestamp}.backup")

        try:
            self.db_path.rename(backup_path)
            logger.info(f"Created database backup: {backup_path}")

            # make a new empty file
            self.db_path.touch()
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create database backup: {e}")
            return None

    async def initialize_db(self):
        """Initialize database with current schema."""
        logger.info("Initializing database...")

        if self.db_type == db.DatabaseType.FILESYSTEM:
            await self.create_backup()

        # Drop existing tables if any
        await db.drop_db()

        # Create tables with current schema
        await db.get_or_create_db(db_path=self.db_path, db_type=self.db_type)

        logger.info("Database initialized with current schema")

    async def check_db(self) -> bool:
        """Check database state and rebuild if schema doesn't match models.

        Returns:
            bool: True if DB is ready for use, False if initialization failed
        """
        try:
            _, session_maker = await db.get_or_create_db(db_path=self.db_path, db_type=self.db_type)
            async with db.scoped_session(session_maker) as db_session:
                # Check actual schema matches
                matches, differences = await check_schema_matches_models(db_session)
                if not matches:
                    logger.warning("Database schema does not match models:")
                    for diff in differences:
                        logger.warning(f"  {diff}")
                    logger.info("Rebuilding database to match current models...")
                    await self.initialize_db()
                    return True

                logger.info("Database schema matches models")
                return True

        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            return False

    async def cleanup_backups(self, keep_count: int = 5):
        """Clean up old database backups, keeping the N most recent."""

        backup_pattern = "*.backup"  # Use relative pattern
        backups = sorted(
            self.db_path.parent.glob(backup_pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        # Remove old backups
        for backup in backups[keep_count:]:
            try:
                backup.unlink()
                logger.debug(f"Removed old backup: {backup}")
            except Exception as e:
                logger.error(f"Failed to remove backup {backup}: {e}")
