"""Functions for managing database migrations."""

import asyncio
from pathlib import Path
from loguru import logger
from alembic.config import Config
from alembic import command


def get_alembic_config() -> Config:  # pragma: no cover
    """Get alembic config with correct paths."""
    migrations_path = Path(__file__).parent
    alembic_ini = migrations_path.parent.parent.parent / "alembic.ini"

    config = Config(alembic_ini)
    config.set_main_option("script_location", str(migrations_path))
    return config


async def reset_database():  # pragma: no cover
    """Drop and recreate all tables."""
    logger.info("Resetting database...")
    config = get_alembic_config()

    def _reset(cfg):
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

    await asyncio.get_event_loop().run_in_executor(None, _reset, config)
