"""Database dependency injection for basic-memory.

This module provides database-related dependencies:
- Engine and session maker factories
- Session dependencies for request handling
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from basic_memory import db


async def get_engine_factory(
    request: Request,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get cached engine and session maker from app state.

    For API requests, returns cached connections from app.state for optimal performance.
    For non-API contexts (CLI), falls back to direct database connection.
    """
    # Try to get cached connections from app state (API context)
    if (
        hasattr(request, "app")
        and hasattr(request.app.state, "engine")
        and hasattr(request.app.state, "session_maker")
    ):
        return request.app.state.engine, request.app.state.session_maker

    # Fallback for non-API contexts (CLI): config comes from the composition
    # root rather than a ConfigManager read here.
    logger.debug("Using fallback database connection for non-API context")
    # Deferred import: importing basic_memory.api at module scope re-enters this
    # package via api.app -> routers -> deps and fails as a circular import.
    from basic_memory.api.container import resolve_container

    app_config = resolve_container().config
    engine, session_maker = await db.get_or_create_db(app_config.database_path)
    return engine, session_maker


EngineFactoryDep = Annotated[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]], Depends(get_engine_factory)
]


async def get_session_maker(engine_factory: EngineFactoryDep) -> async_sessionmaker[AsyncSession]:
    """Get session maker."""
    _, session_maker = engine_factory
    return session_maker


SessionMakerDep = Annotated[async_sessionmaker, Depends(get_session_maker)]


async def get_session(session_maker: SessionMakerDep) -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped SQLAlchemy session."""
    async with db.scoped_session(session_maker) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
