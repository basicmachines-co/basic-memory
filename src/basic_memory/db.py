import asyncio
from contextlib import asynccontextmanager
from enum import Enum, auto
from pathlib import Path
from typing import AsyncGenerator, Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
    async_scoped_session,
)

from basic_memory.models import Base


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

        return f"sqlite+aiosqlite:///{db_path}"


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


async def init_db(session: AsyncSession):
    """Initialize database with required tables."""
    await session.execute(text("PRAGMA foreign_keys=ON"))
    conn = await session.connection()
    await conn.run_sync(Base.metadata.create_all)
    await session.commit()


async def get_or_create_db(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Get or create database engine and session maker."""
    global _engine, _session_maker
    
    if _engine is None:
        db_url = DatabaseType.get_db_url(db_path, db_type)
        logger.debug(f"Creating engine for db_url: {db_url}")
        _engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False)

        # Initialize database
        logger.debug("Initializing database...")
        async with scoped_session(_session_maker) as db_session:
            await init_db(db_session)

    return _engine, _session_maker


async def shutdown_db():
    """Clean up database connections."""
    global _engine, _session_maker
    
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_maker = None


@asynccontextmanager
async def engine_session_factory(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
    init: bool = True,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create engine and session factory.
    
    Note: This is primarily used for testing where we want a fresh database
    for each test. For production use, use get_or_create_db() instead.
    """
    db_url = DatabaseType.get_db_url(db_path, db_type)
    logger.debug(f"Creating engine for db_url: {db_url}")
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)

        if init:
            logger.debug("Initializing database...")
            async with scoped_session(factory) as db_session:
                await init_db(db_session)

        yield engine, factory
    finally:
        await engine.dispose()