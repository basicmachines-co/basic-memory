"""Tests for the 'basic-memory orphans' CLI command."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

# Register the orphans command on the shared app.
import basic_memory.cli.commands.orphans as orphans_cmd  # noqa: F401

runner = CliRunner()

_MOCK_PROJECT_ITEM = MagicMock()
_MOCK_PROJECT_ITEM.name = "test-project"
_MOCK_PROJECT_ITEM.external_id = "11111111-1111-1111-1111-111111111111"

_ORPHAN_ENTITIES = [
    {
        "external_id": "aaaa-1111",
        "title": "Isolated Note",
        "file_path": "notes/isolated.md",
        "note_type": "note",
    },
    {
        "external_id": "bbbb-2222",
        "title": "Dangling Spec",
        "file_path": "specs/dangling.md",
        "note_type": "spec",
    },
]


def _mock_config_manager():
    mock_cm = MagicMock()
    mock_cm.default_project = "test-project"
    return mock_cm


@asynccontextmanager
async def _fake_get_client(project_name=None):
    yield MagicMock()


@patch("basic_memory.cli.commands.orphans.ConfigManager")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_json_output(mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls):
    """bm orphans --json outputs a JSON array of entity objects."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client

    mock_knowledge_instance = AsyncMock()
    mock_knowledge_instance.get_orphans.return_value = _ORPHAN_ENTITIES
    mock_knowledge_cls.return_value = mock_knowledge_instance

    result = runner.invoke(cli_app, ["orphans", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    start = result.output.index("[")
    data = json.loads(result.output[start:])
    assert len(data) == 2
    titles = {e["title"] for e in data}
    assert "Isolated Note" in titles
    assert "Dangling Spec" in titles


@patch("basic_memory.cli.commands.orphans.ConfigManager")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_table_output(mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls):
    """bm orphans (default) renders a Rich table with titles and paths."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client

    mock_knowledge_instance = AsyncMock()
    mock_knowledge_instance.get_orphans.return_value = _ORPHAN_ENTITIES
    mock_knowledge_cls.return_value = mock_knowledge_instance

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
    """bm orphans prints a success message when no orphans are found."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client

    mock_knowledge_instance = AsyncMock()
    mock_knowledge_instance.get_orphans.return_value = []
    mock_knowledge_cls.return_value = mock_knowledge_instance

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No orphan entities" in result.output
