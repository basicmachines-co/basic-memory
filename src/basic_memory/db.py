import asyncio
from contextlib import asynccontextmanager
from enum import Enum, auto
from pathlib import Path
from typing import AsyncGenerator

from basic_memory.config import BasicMemoryConfig, ConfigManager
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

# Module level state - now supports multiple databases
_engines: dict[str, AsyncEngine] = {}
_session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}
_migrations_completed: dict[str, bool] = {}


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


def _create_engine_and_session(
    db_path: Path, db_type: DatabaseType = DatabaseType.FILESYSTEM
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Internal helper to create engine and session maker."""
    db_url = DatabaseType.get_db_url(db_path, db_type)
    logger.debug(f"Creating engine for db_url: {db_url}")
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_maker


async def get_or_create_db(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
    ensure_migrations: bool = True,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get or create database engine and session maker for a specific database path."""
    global _engines, _session_makers, _migrations_completed
    
    # Use string path as key for the cache
    db_key = str(db_path)
    
    # Check if we already have an engine for this database
    if db_key not in _engines:
        engine, session_maker = _create_engine_and_session(db_path, db_type)
        _engines[db_key] = engine
        _session_makers[db_key] = session_maker
        
        # Run migrations automatically unless explicitly disabled
        if ensure_migrations and not _migrations_completed.get(db_key, False):
            app_config = ConfigManager().config
            await run_migrations_for_db(app_config, db_path, db_type)
            _migrations_completed[db_key] = True
    
    # Get the engine and session maker for this database
    engine = _engines.get(db_key)
    session_maker = _session_makers.get(db_key)
    
    # These checks should never fail since we just created them if they were missing
    if engine is None:
        logger.error("Failed to create database engine", db_path=str(db_path))
        raise RuntimeError("Database engine initialization failed")
    
    if session_maker is None:
        logger.error("Failed to create session maker", db_path=str(db_path))
        raise RuntimeError("Session maker initialization failed")
    
    return engine, session_maker


async def run_migrations_for_db(
    app_config: BasicMemoryConfig, 
    db_path: Path,
    database_type: DatabaseType = DatabaseType.FILESYSTEM
) -> None:  # pragma: no cover
    """Run migrations for a specific database."""
    from basic_memory.models import Base
    
    # Get the engine for this specific database
    db_key = str(db_path)
    engine = _engines.get(db_key)
    
    if not engine:
        logger.error(f"No engine found for database: {db_path}")
        return
    
    # Create all tables directly using SQLAlchemy for this database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info(f"Database tables created for: {db_path}")
    
    # Initialize the search index
    session_maker = _session_makers.get(db_key)
    if session_maker:
        # The project_id is not used for init_search_index, so we pass a dummy value
        await SearchRepository(session_maker, 1).init_search_index()
        logger.info(f"Search index created for: {db_path}")
    else:
        logger.error(f"No session maker found for database: {db_path}")


async def shutdown_db() -> None:  # pragma: no cover
    """Clean up all database connections."""
    global _engines, _session_makers, _migrations_completed
    
    for db_key, engine in _engines.items():
        if engine:
            await engine.dispose()
            logger.debug(f"Disposed engine for: {db_key}")
    
    _engines.clear()
    _session_makers.clear()
    _migrations_completed.clear()


@asynccontextmanager
async def engine_session_factory(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.MEMORY,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create engine and session factory.

    Note: This is primarily used for testing where we want a fresh database
    for each test. For production use, use get_or_create_db() instead.
    """

    global _engine, _session_maker, _migrations_completed

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
    app_config: BasicMemoryConfig, database_type=DatabaseType.FILESYSTEM, force: bool = False
):  # pragma: no cover
    """Run any pending alembic migrations for the default database."""
    global _migrations_completed
    
    # Get the database path to use as key
    db_key = str(app_config.database_path)

    # Skip if migrations already completed unless forced
    if _migrations_completed.get(db_key, False) and not force:
        logger.debug("Migrations already completed for this database, skipping")
        return

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

        # Get session maker - ensure we don't trigger recursive migration calls
        if _session_maker is None:
            _, session_maker = _create_engine_and_session(app_config.database_path, database_type)
        else:
            session_maker = _session_maker

        # initialize the search Index schema
        # the project_id is not used for init_search_index, so we pass a dummy value
        await SearchRepository(session_maker, 1).init_search_index()

        # Mark migrations as completed for this database
        _migrations_completed[db_key] = True
    except Exception as e:  # pragma: no cover
        logger.error(f"Error running migrations: {e}")
        raise
