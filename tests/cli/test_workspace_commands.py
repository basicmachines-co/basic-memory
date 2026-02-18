"""Tests for workspace CLI commands."""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.schemas.cloud import WorkspaceInfo

# Importing registers workspace commands on the shared app instance.
import basic_memory.cli.commands.workspace as workspace_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


def test_workspace_list_prints_available_workspaces(runner, monkeypatch):
    async def fake_get_available_workspaces(context=None):
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="personal",
                name="Personal",
                role="owner",
            ),
            WorkspaceInfo(
                tenant_id="22222222-2222-2222-2222-222222222222",
                workspace_type="organization",
                name="Team",
                role="editor",
            ),
        ]

    monkeypatch.setattr(workspace_cmd, "get_available_workspaces", fake_get_available_workspaces)

    result = runner.invoke(app, ["workspace", "list"])

    assert result.exit_code == 0
    assert "Available Workspaces" in result.stdout
    assert "Personal" in result.stdout
    assert "Team" in result.stdout
    assert "11111111-1111-1111-1111-111111111111" in result.stdout


def test_workspaces_alias_matches_workspace_list_output(runner, monkeypatch):
    async def fake_get_available_workspaces(context=None):
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="personal",
                name="Personal",
                role="owner",
            )
        ]

    monkeypatch.setattr(workspace_cmd, "get_available_workspaces", fake_get_available_workspaces)

    list_result = runner.invoke(app, ["workspace", "list"])
    alias_result = runner.invoke(app, ["workspaces"])

    assert list_result.exit_code == 0
    assert alias_result.exit_code == 0
    assert list_result.stdout == alias_result.stdout


def test_workspace_list_requires_oauth_login_message(runner, monkeypatch):
    async def fail_get_available_workspaces(context=None):  # pragma: no cover
        raise RuntimeError("Workspace discovery requires OAuth login. Run 'bm cloud login' first.")

    monkeypatch.setattr(workspace_cmd, "get_available_workspaces", fail_get_available_workspaces)

    result = runner.invoke(app, ["workspace", "list"])

    assert result.exit_code == 1
    assert "Workspace discovery requires OAuth login" in result.stdout
    assert "bm cloud login" in result.stdout
