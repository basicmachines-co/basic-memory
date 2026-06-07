"""Integration test for the asyncpg engine-dispose race (issue #831 / #877).

On the Postgres backend (``postgresql+asyncpg``), the "open async engine -> work
-> await engine.dispose()" cycle could crash with
``IndexError: pop from an empty deque`` raised from SQLAlchemy's async dispose
machinery as it races event-loop teardown. This made Postgres + file watcher
unusable (container restart loop).

These tests exercise the real create-engine -> query -> dispose cycle against a
live Postgres instance (testcontainers) for many iterations. The race is
timing-dependent, so we loop enough to be meaningful and assert clean
completion. They are skipped on SQLite, which is unaffected.

Run with:
    BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest \
        test-int/test_postgres_dispose_cycle.py -q
"""

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import DatabaseBackend
from basic_memory.db import DatabaseType, _create_postgres_engine


@pytest.mark.asyncio
async def test_create_query_dispose_cycle_is_clean(app_config, db_backend):
    """Repeatedly create -> query -> dispose an asyncpg engine without crashing."""
    if db_backend != "postgres":
        pytest.skip("Postgres-specific test - asyncpg dispose race does not affect SQLite")

    db_url = DatabaseType.get_db_url(app_config.database_path, DatabaseType.POSTGRES, app_config)

    # Loop enough iterations to make the timing-dependent dispose race observable.
    for _ in range(50):
        engine = _create_postgres_engine(db_url, app_config)
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        # This dispose is the call site that raced loop teardown in #831/#877.
        await engine.dispose()


@pytest.mark.asyncio
async def test_shutdown_db_dispose_is_clean(app_config, config_manager, db_backend):
    """Repeated get_or_create_db -> query -> shutdown_db cycles complete cleanly.

    Exercises the shielded ``shutdown_db`` teardown path against asyncpg. The
    autouse ``cleanup_global_db_after_test`` fixture in conftest restores module
    state afterwards.
    """
    if db_backend != "postgres":
        pytest.skip("Postgres-specific test - asyncpg dispose race does not affect SQLite")

    assert app_config.database_backend == DatabaseBackend.POSTGRES

    for _ in range(25):
        engine, session_maker = await db.get_or_create_db(
            app_config.database_path,
            db_type=DatabaseType.POSTGRES,
            ensure_migrations=False,
            config=app_config,
        )
        async with db.scoped_session(session_maker) as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        # Shielded dispose - must not surface the asyncpg deque race.
        await db.shutdown_db()
