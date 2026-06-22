"""Tests for MCP container composition root."""

import pytest

from basic_memory.mcp.container import (
    McpContainer,
    get_container,
    set_container,
)
from basic_memory.runtime import RuntimeMode


class TestMcpContainer:
    """Tests for McpContainer."""

    def test_create_from_config(self, app_config):
        """Container can be created from config manager."""
        container = McpContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.config == app_config
        assert container.mode == RuntimeMode.LOCAL

    def test_should_watch_files_when_enabled_local_mode(self, app_config):
        """Local watching should be enabled in local mode when config says so."""
        app_config.index_changes = True
        container = McpContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.should_watch_files is True

    def test_should_not_watch_files_when_disabled(self, app_config):
        """Local watching should be disabled when config says so."""
        app_config.index_changes = False
        container = McpContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.should_watch_files is False

    def test_should_not_watch_files_in_test_mode(self, app_config):
        """Local watching should be disabled in test mode regardless of config."""
        app_config.index_changes = True
        container = McpContainer(config=app_config, mode=RuntimeMode.TEST)
        assert container.should_watch_files is False

    def test_should_not_watch_files_in_cloud_mode(self, app_config):
        """Local watching should be disabled in cloud mode."""
        app_config.index_changes = True
        container = McpContainer(config=app_config, mode=RuntimeMode.CLOUD)
        assert container.should_watch_files is False


class TestWatchSkipReason:
    """Tests for watch_skip_reason property."""

    def test_skip_reason_in_test_mode(self, app_config):
        """Returns test message when in test mode."""
        container = McpContainer(config=app_config, mode=RuntimeMode.TEST)
        assert container.watch_skip_reason == "Test environment detected"

    def test_skip_reason_in_cloud_mode(self, app_config):
        """Returns cloud message when in cloud mode."""
        container = McpContainer(config=app_config, mode=RuntimeMode.CLOUD)
        assert container.watch_skip_reason == "Cloud mode enabled"

    def test_skip_reason_when_watch_disabled(self, app_config):
        """Returns disabled message when local watching is disabled."""
        app_config.index_changes = False
        container = McpContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.watch_skip_reason == "Local file watching disabled"

    def test_no_skip_reason_when_should_watch(self, app_config):
        """Returns None when local watching should run."""
        app_config.index_changes = True
        container = McpContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.watch_skip_reason is None


class TestContainerAccessors:
    """Tests for container get/set functions."""

    def test_get_container_raises_when_not_set(self, monkeypatch):
        """get_container raises RuntimeError when container not initialized."""
        import basic_memory.mcp.container as container_module

        monkeypatch.setattr(container_module, "_container", None)

        with pytest.raises(RuntimeError, match="MCP container not initialized"):
            get_container()

    def test_set_and_get_container(self, app_config, monkeypatch):
        """set_container allows get_container to return the container."""
        import basic_memory.mcp.container as container_module

        container = McpContainer(config=app_config, mode=RuntimeMode.LOCAL)
        monkeypatch.setattr(container_module, "_container", None)

        set_container(container)
        assert get_container() is container
