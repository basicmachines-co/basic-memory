import asyncio
import os
from contextlib import asynccontextmanager
from enum import Enum, auto
from pathlib import Path
from typing import AsyncGenerator, Optional

from basic_memory.config import BasicMemoryConfig, ConfigManager, DatabaseBackend
from alembic import command
from alembic.config import Config

from loguru import logger
from sqlalchemy import text, event
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
    async_scoped_session,
)
from sqlalchemy.pool import NullPool

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

# Module level state
_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


class DatabaseType(Enum):
    """Types of supported databases."""

    MEMORY = auto()
    FILESYSTEM = auto()
    POSTGRES = auto()

    @classmethod
    def get_db_url(
        cls, db_path: Path, db_type: "DatabaseType", config: Optional[BasicMemoryConfig] = None
    ) -> str:
        """Get SQLAlchemy URL for database path.

        Args:
            db_path: Path to SQLite database file (ignored for Postgres)
            db_type: Type of database (MEMORY, FILESYSTEM, or POSTGRES)
            config: Optional config to check for database backend and URL

        Returns:
            SQLAlchemy connection URL
        """
        # Load config if not provided
        if config is None:
            config = ConfigManager().config

        # Handle explicit Postgres type
        if db_type == cls.POSTGRES:
            if not config.database_url:
                raise ValueError("DATABASE_URL must be set when using Postgres backend")
            logger.info(f"Using Postgres database: {config.database_url}")
            return config.database_url

        # Check if Postgres backend is configured (for backward compatibility)
        if config.database_backend == DatabaseBackend.POSTGRES:
            if not config.database_url:
                raise ValueError("DATABASE_URL must be set when using Postgres backend")
            logger.info(f"Using Postgres database: {config.database_url}")
            return config.database_url

        # SQLite databases
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
        # Only enable foreign keys for SQLite (Postgres has them enabled by default)
        # Detect database type from session's bind (engine) dialect
        engine = session.get_bind()
        dialect_name = engine.dialect.name

        if dialect_name == "sqlite":
            await session.execute(text("PRAGMA foreign_keys=ON"))

        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
        await factory.remove()


def _configure_sqlite_connection(dbapi_conn, enable_wal: bool = True) -> None:
    """Configure SQLite connection with WAL mode and optimizations.

    Args:
        dbapi_conn: Database API connection object
        enable_wal: Whether to enable WAL mode (should be False for in-memory databases)
    """
    cursor = dbapi_conn.cursor()
    try:
        # Enable WAL mode for better concurrency (not supported for in-memory databases)
        if enable_wal:
            cursor.execute("PRAGMA journal_mode=WAL")
        # Set busy timeout to handle locked databases
        cursor.execute("PRAGMA busy_timeout=10000")  # 10 seconds
        # Optimize for performance
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        # Windows-specific optimizations
        if os.name == "nt":
            cursor.execute("PRAGMA locking_mode=NORMAL")  # Ensure normal locking on Windows
    except Exception as e:
        # Log but don't fail - some PRAGMAs may not be supported
        logger.warning(f"Failed to configure SQLite connection: {e}")
    finally:
        cursor.close()


def _create_sqlite_engine(db_url: str, db_type: DatabaseType) -> AsyncEngine:
    """Create SQLite async engine with appropriate configuration.

    Args:
        db_url: SQLite connection URL
        db_type: Database type (MEMORY or FILESYSTEM)

    Returns:
        Configured async engine for SQLite
    """
    # Configure connection args with Windows-specific settings
    connect_args: dict[str, bool | float | None] = {"check_same_thread": False}

    # Add Windows-specific parameters to improve reliability
    if os.name == "nt":  # Windows
        connect_args.update(
            {
                "timeout": 30.0,  # Increase timeout to 30 seconds for Windows
                "isolation_level": None,  # Use autocommit mode
            }
        )
        # Use NullPool for Windows filesystem databases to avoid connection pooling issues
        # Important: Do NOT use NullPool for in-memory databases as it will destroy the database
        # between connections
        if db_type == DatabaseType.FILESYSTEM:
            engine = create_async_engine(
                db_url,
                connect_args=connect_args,
                poolclass=NullPool,  # Disable connection pooling on Windows
                echo=False,
            )
        else:
            # In-memory databases need connection pooling to maintain state
            engine = create_async_engine(db_url, connect_args=connect_args)
    else:
        engine = create_async_engine(db_url, connect_args=connect_args)

    # Enable WAL mode for better concurrency and reliability
    # Note: WAL mode is not supported for in-memory databases
    enable_wal = db_type != DatabaseType.MEMORY

    @event.listens_for(engine.sync_engine, "connect")
    def enable_wal_mode(dbapi_conn, connection_record):
        """Enable WAL mode on each connection."""
        _configure_sqlite_connection(dbapi_conn, enable_wal=enable_wal)

    return engine


def _create_postgres_engine(db_url: str, config: BasicMemoryConfig) -> AsyncEngine:
    """Create Postgres async engine with appropriate configuration.

    Args:
        db_url: Postgres connection URL (postgresql+asyncpg://...)
        config: BasicMemoryConfig with pool settings

    Returns:
        Configured async engine for Postgres
    """
    # Use NullPool connection issues.
    # Assume connection pooler like PgBouncer handles connection pooling.
    engine = create_async_engine(
        db_url,
        echo=False,
        poolclass=NullPool,  # No pooling - fresh connection per request
        connect_args={
            # Disable statement cache to avoid issues with prepared statements on reconnect
            "statement_cache_size": 0,
            # Allow 30s for commands (Neon cold start can take 2-5s, sometimes longer)
            "command_timeout": 30,
            # Allow 30s for initial connection (Neon wake-up time)
            "timeout": 30,
            "server_settings": {
                "application_name": "basic-memory",
                # Statement timeout for queries (30s to allow for cold start)
                "statement_timeout": "30s",
            },
        },
    )
    logger.debug("Created Postgres engine with NullPool (no connection pooling)")

    return engine


def _create_engine_and_session(
    db_path: Path, db_type: DatabaseType = DatabaseType.FILESYSTEM
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Internal helper to create engine and session maker.

    Args:
        db_path: Path to database file (used for SQLite, ignored for Postgres)
        db_type: Type of database (MEMORY, FILESYSTEM, or POSTGRES)

    Returns:
        Tuple of (engine, session_maker)
    """
    config = ConfigManager().config
    db_url = DatabaseType.get_db_url(db_path, db_type, config)
    logger.debug(f"Creating engine for db_url: {db_url}")

    # Delegate to backend-specific engine creation
    # Check explicit POSTGRES type first, then config setting
    if db_type == DatabaseType.POSTGRES or config.database_backend == DatabaseBackend.POSTGRES:
        engine = _create_postgres_engine(db_url, config)
    else:
        engine = _create_sqlite_engine(db_url, db_type)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_maker


async def get_or_create_db(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
    ensure_migrations: bool = True,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get or create database engine and session maker."""
    global _engine, _session_maker

    if _engine is None:
        _engine, _session_maker = _create_engine_and_session(db_path, db_type)

        # Run migrations automatically unless explicitly disabled
        if ensure_migrations:
            app_config = ConfigManager().config
            await run_migrations(app_config, db_type)

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

    # Use the same helper function as production code
    _engine, _session_maker = _create_engine_and_session(db_path, db_type)

    try:
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
    """Run any pending alembic migrations.

    Note: Alembic tracks which migrations have been applied via the alembic_version table,
    so it's safe to call this multiple times - it will only run pending migrations.
    """
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

        # Get the correct database URL based on backend configuration
        # No URL conversion needed - env.py now handles both async and sync engines
        db_url = DatabaseType.get_db_url(app_config.database_path, database_type, app_config)
        config.set_main_option("sqlalchemy.url", db_url)

        command.upgrade(config, "head")
        logger.info("Migrations completed successfully")

        # Get session maker - ensure we don't trigger recursive migration calls
        if _session_maker is None:
            _, session_maker = _create_engine_and_session(app_config.database_path, database_type)
        else:
            session_maker = _session_maker

        # Initialize the search index schema
        # For SQLite: Create FTS5 virtual table
        # For Postgres: No-op (tsvector column added by migrations)
        # The project_id is not used for init_search_index, so we pass a dummy value
        if (
            database_type == DatabaseType.POSTGRES
            or app_config.database_backend == DatabaseBackend.POSTGRES
        ):
            await PostgresSearchRepository(session_maker, 1).init_search_index()
        else:
            await SQLiteSearchRepository(session_maker, 1).init_search_index()
    except Exception as e:  # pragma: no cover
        logger.error(f"Error running migrations: {e}")
        raise
