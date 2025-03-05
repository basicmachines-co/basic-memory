"""Functions for managing database migrations."""

from pathlib import Path
from loguru import logger
from alembic.config import Config
from alembic import command


def get_alembic_config() -> Config:  # pragma: no cover
    """Get alembic config with correct paths."""
    migrations_path = Path(__file__).parent
    alembic_ini = migrations_path / "alembic.ini"

    config = Config(alembic_ini)
    config.set_main_option("script_location", str(migrations_path))
    return config


def reset_database():  # pragma: no cover
    """Drop and recreate all tables."""
    logger.info("Resetting database...")
    config = get_alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
