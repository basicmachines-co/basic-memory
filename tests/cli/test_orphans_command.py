"""Tests for the 'basic-memory orphans' CLI command."""

import json
from contextlib import asynccontextmanager, nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.server.fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.schemas.v2.graph import GraphNode

import basic_memory.cli.commands.orphans as orphans_cmd  # noqa: F401

runner = CliRunner()

_MOCK_PROJECT_ITEM = MagicMock()
_MOCK_PROJECT_ITEM.name = "test-project"
_MOCK_PROJECT_ITEM.external_id = "11111111-1111-1111-1111-111111111111"

_ORPHAN_ENTITIES = [
    GraphNode(
        external_id="aaaa-1111",
        title="Isolated Note",
        file_path="notes/isolated.md",
        note_type="note",
    ),
    GraphNode(
        external_id="bbbb-2222",
        title="Dangling Spec",
        file_path="specs/dangling.md",
        note_type="spec",
    ),
]


def _mock_config_manager():
    mock_config = MagicMock()
    mock_config.default_project = "test-project"
    return mock_config


@asynccontextmanager
async def _fake_get_client(project_name=None):
    yield MagicMock()


@patch("basic_memory.cli.commands.orphans.run_orphans", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.force_routing")
def test_orphans_preserves_project_routing_by_default(mock_force_routing, mock_run_orphans):
    """Default invocation keeps routing implicit so project mode can choose local/cloud."""
    mock_force_routing.return_value = nullcontext()
    mock_run_orphans.return_value = ("test-project", [])

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_force_routing.assert_called_once_with(local=False, cloud=False)


@patch("basic_memory.cli.commands.orphans.ConfigManager")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_json_output(mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls):
    """basic-memory orphans --json outputs a JSON array of orphan entity objects."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = _ORPHAN_ENTITIES
    mock_knowledge_cls.return_value = mock_knowledge

    result = runner.invoke(cli_app, ["orphans", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    json_start = result.output.rfind("[\n")
    data = json.loads(result.output[json_start:])
    titles = {entity["title"] for entity in data}
    assert titles == {"Isolated Note", "Dangling Spec"}
    mock_get_client.assert_called_once_with(project_name="test-project")


@patch("basic_memory.cli.commands.orphans.ConfigManager")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_table_output(
    mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls
):
    """basic-memory orphans renders a table with orphan titles and paths."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = _ORPHAN_ENTITIES
    mock_knowledge_cls.return_value = mock_knowledge

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Isolated Note" in result.output
    assert "Dangling Spec" in result.output
    assert "notes/isolated.md" in result.output


@patch("basic_memory.cli.commands.orphans.ConfigManager")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_no_results(mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls):
    """basic-memory orphans prints a success message when no orphans are found."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = []
    mock_knowledge_cls.return_value = mock_knowledge

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No orphan entities" in result.output


@patch("basic_memory.cli.commands.orphans.run_orphans", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.force_routing")
def test_orphans_value_error(mock_force_routing, mock_run_orphans):
    """User-facing command errors are printed and exit with failure."""
    mock_force_routing.return_value = nullcontext()
    mock_run_orphans.side_effect = ValueError("project not found")

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 1
    assert "Error: project not found" in result.output


@patch("basic_memory.cli.commands.orphans.run_orphans", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.force_routing")
def test_orphans_tool_error_json_output(mock_force_routing, mock_run_orphans):
    """User-facing command errors are JSON formatted when requested."""
    mock_force_routing.return_value = nullcontext()
    mock_run_orphans.side_effect = ToolError("cloud request failed")

    result = runner.invoke(cli_app, ["orphans", "--json"])

    assert result.exit_code == 1
    json_start = result.output.rfind("{\n")
    assert json.loads(result.output[json_start:]) == {"error": "cloud request failed"}
