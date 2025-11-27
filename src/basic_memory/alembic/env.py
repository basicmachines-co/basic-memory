"""Alembic environment configuration."""

import asyncio
import os
from logging.config import fileConfig

# Allow nested event loops (needed for pytest-asyncio and other async contexts)
# Note: nest_asyncio doesn't work with uvloop, so we handle that case separately
try:
    import nest_asyncio

    nest_asyncio.apply()
except (ImportError, ValueError):
    # nest_asyncio not available or can't patch this loop type (e.g., uvloop)
    pass

from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

from basic_memory.config import ConfigManager

# set config.env to "test" for pytest to prevent logging to file in utils.setup_logging()
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
def include_object(object, name, type_, reflected, compare_to):
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
    await connectable.dispose()


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
        # Try to run async migrations
        # nest_asyncio allows asyncio.run() from within event loops, but doesn't work with uvloop
        try:
            asyncio.run(run_async_migrations(connectable))
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                # We're in a running event loop (likely uvloop) - need to use a different approach
                # Create a new thread to run the async migrations
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
            else:
                raise
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
