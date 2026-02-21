"""Regression tests for project info error handling."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import typer
from mcp.server.fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.command_utils as command_utils
import basic_memory.cli.commands.project as project_cmd  # noqa: F401

runner = CliRunner()


@pytest.mark.asyncio
async def test_get_project_info_cloud_config_error_has_clear_message(monkeypatch, capsys):
    """Cloud internal proxy config failures should surface actionable guidance."""

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield object()

    async def fake_get_active_project(client, project, context):
        return SimpleNamespace(external_id="proj-123")

    async def fake_call_get(client, url):
        raise ToolError("Internal proxy error: Project 'demo' not found in configuration")

    monkeypatch.setattr(command_utils, "get_client", fake_get_client)
    monkeypatch.setattr(command_utils, "get_active_project", fake_get_active_project)
    monkeypatch.setattr(command_utils, "call_get", fake_call_get)

    with pytest.raises(typer.Exit) as exc:
        await command_utils.get_project_info("demo")

    assert exc.value.exit_code == 1
    captured = capsys.readouterr()
    combined_output = captured.out + captured.err
    assert "Project info failed: cloud returned an internal configuration error" in combined_output
    assert "bm project list" in combined_output
    assert "--cloud" in combined_output


def test_project_info_does_not_print_wrapper_exit_code(monkeypatch):
    """project info should not append a secondary 'Error getting project info: 1' line."""

    async def fake_get_project_info(_project_name: str):
        raise typer.Exit(1)

    monkeypatch.setattr(project_cmd, "get_project_info", fake_get_project_info)

    result = runner.invoke(app, ["project", "info", "demo"])

    assert result.exit_code == 1
    assert "Error getting project info" not in result.output
