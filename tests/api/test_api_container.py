"""Tests for API container composition root."""

import pytest

from basic_memory.api.container import (
    ApiContainer,
    get_container,
    set_container,
)
from basic_memory.runtime import RuntimeMode


class TestApiContainer:
    """Tests for ApiContainer."""

    def test_create_from_config(self, app_config):
        """Container can be created from config manager."""
        container = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.config == app_config
        assert container.mode == RuntimeMode.LOCAL

    def test_should_sync_files_when_enabled_and_not_test(self, app_config):
        """Sync should be enabled when config says so and not in test mode."""
        app_config.sync_changes = True
        container = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.should_sync_files is True

    def test_should_not_sync_files_when_disabled(self, app_config):
        """Sync should be disabled when config says so."""
        app_config.sync_changes = False
        container = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.should_sync_files is False

    def test_should_not_sync_files_in_test_mode(self, app_config):
        """Sync should be disabled in test mode regardless of config."""
        app_config.sync_changes = True
        container = ApiContainer(config=app_config, mode=RuntimeMode.TEST)
        assert container.should_sync_files is False


class TestContainerAccessors:
    """Tests for container get/set functions."""

    def test_get_container_raises_when_not_set(self, monkeypatch):
        """get_container raises RuntimeError when container not initialized."""
        # Clear any existing container
        import basic_memory.api.container as container_module

        monkeypatch.setattr(container_module, "_container", None)

        with pytest.raises(RuntimeError, match="API container not initialized"):
            get_container()

    def test_set_and_get_container(self, app_config, monkeypatch):
        """set_container allows get_container to return the container."""
        import basic_memory.api.container as container_module

        container = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
        monkeypatch.setattr(container_module, "_container", None)

        set_container(container)
        assert get_container() is container
