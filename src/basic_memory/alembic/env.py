"""Alembic environment configuration."""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from basic_memory.config import ConfigManager, DatabaseBackend

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

    # For Postgres, Alembic needs synchronous driver (psycopg2), not async (asyncpg)
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        # Convert asyncpg URL to psycopg2 URL for Alembic
        sqlalchemy_url = sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://")

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


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
