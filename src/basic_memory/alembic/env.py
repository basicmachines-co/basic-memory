"""Alembic environment configuration."""

import asyncio
import os
import sys
from contextlib import suppress
from logging.config import fileConfig

from loguru import logger
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

from basic_memory.config import ConfigManager
from basic_memory.migration_loop import running_on_uvloop

# Allow nested event loops (needed for pytest-asyncio and other async contexts).
# nest_asyncio cannot patch a uvloop loop or Python 3.14+; in those cases we skip
# it and rely on the thread-based fallback in run_migrations_online() instead
# (see basic_memory.migration_loop for why uvloop must be detected up front).
if sys.version_info < (3, 14) and not running_on_uvloop():
    try:
        import nest_asyncio

        nest_asyncio.apply()
    except (ImportError, ValueError) as exc:
        # Trigger: nest_asyncio is absent (ImportError) or refuses to patch the
        # running loop (ValueError).
        # Outcome: log at DEBUG (observable, not noisy) and fall through to the
        # thread-based migration fallback.
        logger.debug(
            f"nest_asyncio not applied ({exc!r}); using thread-based migration fallback"
        )

# Trigger: only set test env when actually running under pytest
# Why: alembic/env.py is imported during normal operations (MCP server startup, migrations)
#      but we only want test behavior during actual test runs
# Outcome: prevents is_test_env from returning True in production, enabling watch service
if os.getenv("PYTEST_CURRENT_TEST") is not None:
    os.environ["BASIC_MEMORY_ENV"] = "test"

# Import after setting environment variable  # noqa: E402
from basic_memory.models import Base  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Load app config - this will read environment variables (BASIC_MEMORY_DATABASE_BACKEND, etc.)
# due to Pydantic's env_prefix="BASIC_MEMORY_" setting
app_config = ConfigManager().config

# Set the SQLAlchemy URL based on database backend configuration
# If the URL is already set in config (e.g., from run_migrations), use that
# Otherwise, get it from app config
# Note: alembic.ini has a placeholder URL "driver://user:pass@localhost/dbname" that we need to override
current_url = config.get_main_option("sqlalchemy.url")
if not current_url or current_url == "driver://user:pass@localhost/dbname":
    from basic_memory.db import DatabaseType

    sqlalchemy_url = DatabaseType.get_db_url(
        app_config.database_path, DatabaseType.FILESYSTEM, app_config
    )
    config.set_main_option("sqlalchemy.url", sqlalchemy_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


# Add this function to tell Alembic what to include/exclude
def include_object(obj, name, type_, reflected, compare_to):
    # Ignore SQLite FTS tables
    if type_ == "table" and name.startswith("search_index"):
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Execute migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations(connectable):
    """Run migrations asynchronously with AsyncEngine."""
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    # Trigger: startup migrations on the asyncpg backend dispose the engine
    # while the event loop may be tearing down around them.
    # Why: that race surfaces "IndexError: pop from an empty deque" from
    # base_events._run_once (#831/#877); shielding lets dispose finish atomically
    # and suppressing CancelledError keeps a cancelled teardown from re-raising it.
    # Outcome: the migration engine always disposes cleanly. (uvloop is the
    # structural fix for the race; this hardens the teardown path.)
    with suppress(asyncio.CancelledError):
        await asyncio.shield(connectable.dispose())


def _run_async_migrations_with_asyncio_run(connectable) -> None:
    """Run async migrations with asyncio.run while closing failed coroutines.

    Trigger: asyncio.run() may reject execution when another event loop is already active.
    Why: Python raises before awaiting the coroutine, which otherwise leaks a
    RuntimeWarning about an un-awaited coroutine.
    Outcome: close the pending coroutine before bubbling the RuntimeError to the
    fallback path.
    """
    migration_coro = run_async_migrations(connectable)
    try:
        asyncio.run(migration_coro)
    except RuntimeError:
        migration_coro.close()
        raise


def _run_async_migrations_in_thread(connectable) -> None:
    """Run async migrations in a dedicated thread with its own event loop."""
    import concurrent.futures

    def run_in_thread():
        """Run async migrations in a new event loop in a separate thread."""
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            new_loop.run_until_complete(run_async_migrations(connectable))
        finally:
            new_loop.close()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run_in_thread)
        future.result()  # Wait for completion and re-raise any exceptions


def _loop_is_running() -> bool:
    """Check if an event loop is currently running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Trigger: no running loop (get_running_loop raises RuntimeError)
        # Outcome: return False to indicate no loop is running
        return False
    return True


def _run_async_engine_migrations(connectable) -> None:
    """Run async migrations, adapting to whether an event loop is already running."""

    # Trigger: caller may be inside a running event loop (e.g. pytest-asyncio, uvicorn).
    # Why: asyncio.run() raises RuntimeError when nested inside a running loop, so we
    # detect the condition up front via _loop_is_running() instead of catching the error.
    # Outcome: running-loop context -> thread fallback; no loop -> asyncio.run() directly.
    if _loop_is_running():
        # Can't nest asyncio.run() inside a running loop -> offload to a thread.
        _run_async_migrations_in_thread(connectable)
    else:
        # No running loop: asyncio.run() is safe; let any unexpected errors propagate.
        _run_async_migrations_with_asyncio_run(connectable)


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Supports both sync engines (SQLite) and async engines (PostgreSQL with asyncpg).
    """
    # Check if a connection/engine was provided (e.g., from run_migrations)
    connectable = context.config.attributes.get("connection", None)

    if connectable is None:
        # No connection provided, create engine from config
        url = context.config.get_main_option("sqlalchemy.url")

        # Check if it's an async URL (sqlite+aiosqlite or postgresql+asyncpg)
        if url and ("+asyncpg" in url or "+aiosqlite" in url):
            # Create async engine for asyncpg or aiosqlite
            connectable = create_async_engine(
                url,
                poolclass=pool.NullPool,
                future=True,
            )
        else:
            # Create sync engine for regular sqlite or postgresql
            connectable = engine_from_config(
                context.config.get_section(context.config.config_ini_section, {}),
                prefix="sqlalchemy.",
                poolclass=pool.NullPool,
            )

    # Handle async engines (PostgreSQL with asyncpg)
    if isinstance(connectable, AsyncEngine):
        # Trigger: async engines need Alembic work to cross the sync/async boundary.
        # Why: most callers can use asyncio.run(), but running-loop contexts need a thread fallback.
        # Outcome: migrations complete without leaking un-awaited coroutines.
        _run_async_engine_migrations(connectable)
    else:
        # Handle sync engines (SQLite) or sync connections
        if hasattr(connectable, "connect"):
            # It's an engine, get a connection
            with connectable.connect() as connection:
                do_run_migrations(connection)
        else:
            # It's already a connection
            do_run_migrations(connectable)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
