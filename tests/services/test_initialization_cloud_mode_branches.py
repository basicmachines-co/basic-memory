import pytest

from basic_memory.services.initialization import ensure_initialization, initialize_app, initialize_file_sync


@pytest.mark.asyncio
async def test_initialize_app_noop_in_cloud_mode(app_config):
    app_config.cloud_mode = True
    await initialize_app(app_config)


def test_ensure_initialization_noop_in_cloud_mode(app_config):
    app_config.cloud_mode = True
    ensure_initialization(app_config)


@pytest.mark.asyncio
async def test_initialize_file_sync_skips_in_test_env(app_config):
    # app_config fixture uses env="test"
    assert app_config.is_test_env is True
    await initialize_file_sync(app_config)


