import asyncio
from contextlib import asynccontextmanager
from enum import Enum, auto
from pathlib import Path
from typing import AsyncGenerator, Optional

from basic_memory.config import BasicMemoryConfig
from alembic import command
from alembic.config import Config

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
    async_scoped_session,
)

from basic_memory.repository.search_repository import SearchRepository

# Module level state
_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


class DatabaseType(Enum):
    """Types of supported databases."""

    MEMORY = auto()
    FILESYSTEM = auto()

    @classmethod
    def get_db_url(cls, db_path: Path, db_type: "DatabaseType") -> str:
        """Get SQLAlchemy URL for database path."""
        if db_type == cls.MEMORY:
            logger.info("Using in-memory SQLite database")
            return "sqlite+aiosqlite://"

        return f"sqlite+aiosqlite:///{db_path}"  # pragma: no cover


def get_scoped_session_factory(
    session_maker: async_sessionmaker[AsyncSession],
) -> async_scoped_session:
    """Create a scoped session factory scoped to current task."""
    return async_scoped_session(session_maker, scopefunc=asyncio.current_task)


@asynccontextmanager
async def scoped_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Get a scoped session with proper lifecycle management.

    Args:
        session_maker: Session maker to create scoped sessions from
    """
    factory = get_scoped_session_factory(session_maker)
    session = factory()
    try:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
        await factory.remove()


async def get_or_create_db(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get or create database engine and session maker."""
    global _engine, _session_maker

    if _engine is None:
        db_url = DatabaseType.get_db_url(db_path, db_type)
        logger.debug(f"Creating engine for db_url: {db_url}")
        _engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False)

    # These checks should never fail since we just created the engine and session maker
    # if they were None, but we'll check anyway for the type checker
    if _engine is None:
        logger.error("Failed to create database engine", db_path=str(db_path))
        raise RuntimeError("Database engine initialization failed")

    if _session_maker is None:
        logger.error("Failed to create session maker", db_path=str(db_path))
        raise RuntimeError("Session maker initialization failed")

    return _engine, _session_maker


async def shutdown_db() -> None:  # pragma: no cover
    """Clean up database connections."""
    global _engine, _session_maker

    if _engine:
        await _engine.dispose()
        _engine = None
        _session_maker = None


@asynccontextmanager
async def engine_session_factory(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.MEMORY,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create engine and session factory.

    Note: This is primarily used for testing where we want a fresh database
    for each test. For production use, use get_or_create_db() instead.
    """

    global _engine, _session_maker

    db_url = DatabaseType.get_db_url(db_path, db_type)
    logger.debug(f"Creating engine for db_url: {db_url}")

    _engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    try:
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False)

        # Verify that engine and session maker are initialized
        if _engine is None:  # pragma: no cover
            logger.error("Database engine is None in engine_session_factory")
            raise RuntimeError("Database engine initialization failed")

        if _session_maker is None:  # pragma: no cover
            logger.error("Session maker is None in engine_session_factory")
            raise RuntimeError("Session maker initialization failed")

        yield _engine, _session_maker
    finally:
        if _engine:
            await _engine.dispose()
            _engine = None
            _session_maker = None


async def run_migrations(
    app_config: BasicMemoryConfig, database_type=DatabaseType.FILESYSTEM
):  # pragma: no cover
    """Run any pending alembic migrations."""
    logger.info("Running database migrations...")
    try:
        # Get the absolute path to the alembic directory relative to this file
        alembic_dir = Path(__file__).parent / "alembic"
        config = Config()

        # Set required Alembic config options programmatically
        config.set_main_option("script_location", str(alembic_dir))
        config.set_main_option(
            "file_template",
            "%%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s",
        )
        config.set_main_option("timezone", "UTC")
        config.set_main_option("revision_environment", "false")
        config.set_main_option(
            "sqlalchemy.url", DatabaseType.get_db_url(app_config.database_path, database_type)
        )

        command.upgrade(config, "head")
        logger.info("Migrations completed successfully")

        _, session_maker = await get_or_create_db(app_config.database_path, database_type)

        # initialize the search Index schema
        # the project_id is not used for init_search_index, so we pass a dummy value
        await SearchRepository(session_maker, 1).init_search_index()
    except Exception as e:  # pragma: no cover
        logger.error(f"Error running migrations: {e}")
        raise
