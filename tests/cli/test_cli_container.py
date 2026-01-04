"""Tests for CLI container composition root."""

import pytest

from basic_memory.cli.container import (
    CliContainer,
    get_container,
    set_container,
    get_or_create_container,
)
from basic_memory.runtime import RuntimeMode


class TestCliContainer:
    """Tests for CliContainer."""

    def test_create_from_config(self, app_config):
        """Container can be created from config."""
        container = CliContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.config == app_config
        assert container.mode == RuntimeMode.LOCAL

    def test_is_cloud_mode_when_cloud(self, app_config):
        """is_cloud_mode returns True in cloud mode."""
        container = CliContainer(config=app_config, mode=RuntimeMode.CLOUD)
        assert container.is_cloud_mode is True

    def test_is_cloud_mode_when_local(self, app_config):
        """is_cloud_mode returns False in local mode."""
        container = CliContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.is_cloud_mode is False

    def test_is_cloud_mode_when_test(self, app_config):
        """is_cloud_mode returns False in test mode."""
        container = CliContainer(config=app_config, mode=RuntimeMode.TEST)
        assert container.is_cloud_mode is False


class TestContainerAccessors:
    """Tests for container get/set functions."""

    def test_get_container_raises_when_not_set(self, monkeypatch):
        """get_container raises RuntimeError when container not initialized."""
        import basic_memory.cli.container as container_module

        monkeypatch.setattr(container_module, "_container", None)

        with pytest.raises(RuntimeError, match="CLI container not initialized"):
            get_container()

    def test_set_and_get_container(self, app_config, monkeypatch):
        """set_container allows get_container to return the container."""
        import basic_memory.cli.container as container_module

        container = CliContainer(config=app_config, mode=RuntimeMode.LOCAL)
        monkeypatch.setattr(container_module, "_container", None)

        set_container(container)
        assert get_container() is container


class TestGetOrCreateContainer:
    """Tests for get_or_create_container - unique to CLI container."""

    def test_creates_new_when_none_exists(self, monkeypatch):
        """get_or_create_container creates a new container when none exists."""
        import basic_memory.cli.container as container_module

        monkeypatch.setattr(container_module, "_container", None)

        container = get_or_create_container()
        assert container is not None
        assert isinstance(container, CliContainer)

    def test_returns_existing_when_set(self, app_config, monkeypatch):
        """get_or_create_container returns existing container if already set."""
        import basic_memory.cli.container as container_module

        existing = CliContainer(config=app_config, mode=RuntimeMode.LOCAL)
        monkeypatch.setattr(container_module, "_container", existing)

        result = get_or_create_container()
        assert result is existing

    def test_sets_module_level_container(self, monkeypatch):
        """get_or_create_container sets the module-level container."""
        import basic_memory.cli.container as container_module

        monkeypatch.setattr(container_module, "_container", None)

        container = get_or_create_container()

        # Verify it was set at module level
        assert container_module._container is container
        # Verify get_container now works
        assert get_container() is container
