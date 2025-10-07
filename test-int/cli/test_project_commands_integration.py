"""Integration tests for project CLI commands."""

import tempfile
from pathlib import Path

from typer.testing import CliRunner

from basic_memory.cli.main import app


def test_project_list(app_config, test_project, config_manager):
    """Test 'bm project list' command shows projects."""
    runner = CliRunner()
    result = runner.invoke(app, ["project", "list"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0
    assert "test-project" in result.stdout
    assert "✓" in result.stdout  # default marker


def test_project_info(app_config, test_project, config_manager):
    """Test 'bm project info' command shows project details."""
    runner = CliRunner()
    result = runner.invoke(app, ["project", "info", "test-project"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0
    assert "Basic Memory Project Info" in result.stdout
    assert "test-project" in result.stdout
    assert "Statistics" in result.stdout


def test_project_info_json(app_config, test_project, config_manager):
    """Test 'bm project info --json' command outputs valid JSON."""
    import json

    runner = CliRunner()
    result = runner.invoke(app, ["project", "info", "test-project", "--json"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0

    # Parse JSON to verify it's valid
    data = json.loads(result.stdout)
    assert data["project_name"] == "test-project"
    assert "statistics" in data
    assert "system" in data


def test_project_add_and_remove(app_config, config_manager):
    """Test adding and removing a project."""
    runner = CliRunner()

    # Use a separate temporary directory to avoid nested path conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        new_project_path = Path(temp_dir) / "new-project"
        new_project_path.mkdir()

        # Add project
        result = runner.invoke(app, ["project", "add", "new-project", str(new_project_path)])

        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0
        assert (
            "Project 'new-project' added successfully" in result.stdout
            or "added" in result.stdout.lower()
        )

        # Verify it shows up in list
        result = runner.invoke(app, ["project", "list"])
        assert result.exit_code == 0
        assert "new-project" in result.stdout

        # Remove project
        result = runner.invoke(app, ["project", "remove", "new-project"])
        assert result.exit_code == 0
        assert "removed" in result.stdout.lower() or "deleted" in result.stdout.lower()


def test_project_set_default(app_config, config_manager):
    """Test setting default project."""
    runner = CliRunner()

    # Use a separate temporary directory to avoid nested path conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        new_project_path = Path(temp_dir) / "another-project"
        new_project_path.mkdir()

        # Add a second project
        result = runner.invoke(app, ["project", "add", "another-project", str(new_project_path)])
        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0

        # Set as default
        result = runner.invoke(app, ["project", "default", "another-project"])
        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0
        assert "default" in result.stdout.lower()

        # Verify in list
        result = runner.invoke(app, ["project", "list"])
        assert result.exit_code == 0
        # The new project should have the checkmark now
        lines = result.stdout.split("\n")
        for line in lines:
            if "another-project" in line:
                assert "✓" in line
