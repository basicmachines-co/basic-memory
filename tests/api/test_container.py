"""Tests for the API container composition root."""

from basic_memory.api.container import ApiContainer, installed_container, resolve_container
from basic_memory.runtime.mode import RuntimeMode


class TestApiContainerWatchGating:
    """The API container must gate local file watching like the MCP container.

    Cloud API deployments (BASIC_MEMORY_CLOUD_MODE) resolve to CLOUD mode and must
    not start the local watcher — cloud storage events are handled by cloud adapters.
    """

    def test_should_watch_files_when_enabled_local_mode(self, app_config):
        app_config.index_changes = True
        container = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.should_watch_files is True
        assert container.watch_skip_reason is None

    def test_should_not_watch_files_when_disabled(self, app_config):
        app_config.index_changes = False
        container = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
        assert container.should_watch_files is False

    def test_should_not_watch_files_in_test_mode(self, app_config):
        app_config.index_changes = True
        container = ApiContainer(config=app_config, mode=RuntimeMode.TEST)
        assert container.should_watch_files is False

    def test_should_not_watch_files_in_cloud_mode(self, app_config):
        app_config.index_changes = True
        container = ApiContainer(config=app_config, mode=RuntimeMode.CLOUD)
        assert container.should_watch_files is False
        assert container.watch_skip_reason == "Cloud mode enabled"


def test_installed_container_restores_previous_container(app_config, monkeypatch):
    """Off-lifespan hosts can expose dependencies without replacing API ownership."""
    import basic_memory.api.container as container_module

    previous = ApiContainer(config=app_config, mode=RuntimeMode.LOCAL)
    current = ApiContainer(config=app_config, mode=RuntimeMode.TEST)
    monkeypatch.setattr(container_module, "_container", previous)

    with installed_container(current):
        assert resolve_container() is current

    assert resolve_container() is previous
