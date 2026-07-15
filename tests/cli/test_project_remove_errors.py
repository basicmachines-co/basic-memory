"""Tests for bm project remove error surfacing (#1034).

Large cloud project deletes can fail with httpx transport errors whose str() is
empty, which used to print a detail-free "Error removing project:". The CLI must
never print a blank error.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a minimal config in an isolated HOME."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "env": "dev",
                "projects": {},
                "default_project": "main",
            },
            indent=2,
        )
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    yield config_file


@pytest.fixture
def failing_resolve(monkeypatch):
    """Stub the API client so project resolution raises a chosen exception."""

    @asynccontextmanager
    async def fake_get_client(*, project_name=None, workspace=None):
        yield object()

    state: dict[str, Exception] = {}

    async def fake_resolve_project(self, identifier):
        raise state["error"]

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "resolve_project", fake_resolve_project)
    return state


def test_project_remove_blank_transport_error_renders_repr(runner, mock_config, failing_resolve):
    """str(ReadTimeout('')) is empty — the CLI must fall back to repr (#1034)."""
    failing_resolve["error"] = httpx.ReadTimeout("")

    result = runner.invoke(app, ["project", "remove", "big-project"])

    assert result.exit_code == 1
    assert "Error removing project:" in result.stdout
    # repr fallback: the exception type must be visible instead of a blank message
    assert "ReadTimeout" in result.stdout


def test_project_remove_error_with_message_renders_str(runner, mock_config, failing_resolve):
    """Exceptions with a message keep rendering str(e)."""
    failing_resolve["error"] = ValueError("project is busy")

    result = runner.invoke(app, ["project", "remove", "big-project"])

    assert result.exit_code == 1
    assert "Error removing project: project is busy" in result.stdout
