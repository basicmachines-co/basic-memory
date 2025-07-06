"""Tests for import_claude_projects command."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.import_claude_projects import import_projects  # noqa
from basic_memory.config import get_project_config

# Set up CLI runner
runner = CliRunner()


@pytest.fixture
def sample_project():
    """Sample project data for testing."""
    return {
        "uuid": "test-uuid",
        "name": "Test Project",
        "created_at": "2025-01-05T20:55:32.499880+00:00",
        "updated_at": "2025-01-05T20:56:39.477600+00:00",
        "prompt_template": "# Test Prompt\n\nThis is a test prompt.",
        "docs": [
            {
                "uuid": "doc-uuid-1",
                "filename": "Test Document",
                "content": "# Test Document\n\nThis is test content.",
                "created_at": "2025-01-05T20:56:39.477600+00:00",
            },
            {
                "uuid": "doc-uuid-2",
                "filename": "Another Document",
                "content": "# Another Document\n\nMore test content.",
                "created_at": "2025-01-05T20:56:39.477600+00:00",
            },
        ],
    }


@pytest.fixture
def sample_projects_json(tmp_path, sample_project):
    """Create a sample projects.json file."""
    json_file = tmp_path / "projects.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([sample_project], f)
    return json_file


def test_import_projects_command_file_not_found(tmp_path):
    """Test error handling for nonexistent file."""
    nonexistent = tmp_path / "nonexistent.json"
    result = runner.invoke(app, ["import", "claude", "projects", str(nonexistent)])
    assert result.exit_code == 1
    assert "File not found" in result.output


def test_import_projects_command_success(tmp_path, sample_projects_json, monkeypatch):
    """Test successful project import via command."""
    # Set up test environment
    config = get_project_config()
    config.home = tmp_path

    # Run import
    result = runner.invoke(app, ["import", "claude", "projects", str(sample_projects_json)])
    assert result.exit_code == 0
    assert "Import complete" in result.output
    assert "Imported 2 project documents" in result.output
    assert "Imported 1 prompt templates" in result.output


def test_import_projects_command_invalid_json(tmp_path):
    """Test error handling for invalid JSON."""
    # Create invalid JSON file
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("not json")

    result = runner.invoke(app, ["import", "claude", "projects", str(invalid_file)])
    assert result.exit_code == 1
    assert "Error during import" in result.output


def test_import_projects_with_base_folder(tmp_path, sample_projects_json, monkeypatch):
    """Test import with custom base folder."""
    # Set up test environment
    config = get_project_config()
    config.home = tmp_path
    base_folder = "claude-exports"

    # Run import
    result = runner.invoke(
        app,
        [
            "import",
            "claude",
            "projects",
            str(sample_projects_json),
            "--base-folder",
            base_folder,
        ],
    )
    assert result.exit_code == 0

    # Check files in base folder
    project_dir = tmp_path / base_folder / "Test_Project"
    assert project_dir.exists()
    assert (project_dir / "docs").exists()
    assert (project_dir / "prompt-template.md").exists()


def test_import_project_without_prompt(tmp_path):
    """Test importing project without prompt template."""
    # Create project without prompt
    project = {
        "uuid": "test-uuid",
        "name": "No Prompt Project",
        "created_at": "2025-01-05T20:55:32.499880+00:00",
        "updated_at": "2025-01-05T20:56:39.477600+00:00",
        "docs": [
            {
                "uuid": "doc-uuid-1",
                "filename": "Test Document",
                "content": "# Test Document\n\nContent.",
                "created_at": "2025-01-05T20:56:39.477600+00:00",
            }
        ],
    }

    json_file = tmp_path / "no_prompt.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump([project], f)

    # Set up environment
    config = get_project_config()
    config.home = tmp_path

    # Run import
    result = runner.invoke(app, ["import", "claude", "projects", str(json_file)])
    assert result.exit_code == 0
    assert "Imported 1 project documents" in result.output
    assert "Imported 0 prompt templates" in result.output
