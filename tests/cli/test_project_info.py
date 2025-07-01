"""Tests for the project_info CLI command."""

import json
from datetime import datetime
from unittest.mock import patch, AsyncMock

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.schemas.project_info import (
    ProjectInfoResponse,
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
)


def test_info_stats():
    """Test the 'project info' command with default output."""
    runner = CliRunner()

    # Create mock project info data
    mock_info = ProjectInfoResponse(
        project_name="test-project",
        project_path="/test/path",
        default_project="test-project",
        statistics=ProjectStatistics(
            total_entities=10,
            total_observations=20,
            total_relations=5,
            total_unresolved_relations=1,
            isolated_entities=2,
            entity_types={"note": 8, "concept": 2},
            observation_categories={"tech": 15, "note": 5},
            relation_types={"connects_to": 3, "references": 2},
            most_connected_entities=[],
        ),
        activity=ActivityMetrics(recently_created=[], recently_updated=[], monthly_growth={}),
        system=SystemStatus(
            version="0.13.0",
            database_path="/test/db.sqlite",
            database_size="1.2 MB",
            watch_status=None,
            timestamp=datetime.now(),
        ),
        available_projects={"test-project": {"path": "/test/path"}},
    )

    # Mock the async project_info function
    with patch(
        "basic_memory.cli.commands.project.project_info.fn", new_callable=AsyncMock
    ) as mock_func:
        mock_func.return_value = mock_info

        # Run the command
        result = runner.invoke(cli_app, ["project", "info"])

        # Verify exit code
        assert result.exit_code == 0

        # Check that key data is included in the output
        assert "Basic Memory Project Info" in result.stdout
        assert "test-project" in result.stdout
        assert "Statistics" in result.stdout


def test_info_stats_json():
    """Test the 'project info --json' command for JSON output."""
    runner = CliRunner()

    # Create mock project info data
    mock_info = ProjectInfoResponse(
        project_name="test-project",
        project_path="/test/path",
        default_project="test-project",
        statistics=ProjectStatistics(
            total_entities=10,
            total_observations=20,
            total_relations=5,
            total_unresolved_relations=1,
            isolated_entities=2,
            entity_types={"note": 8, "concept": 2},
            observation_categories={"tech": 15, "note": 5},
            relation_types={"connects_to": 3, "references": 2},
            most_connected_entities=[],
        ),
        activity=ActivityMetrics(recently_created=[], recently_updated=[], monthly_growth={}),
        system=SystemStatus(
            version="0.13.0",
            database_path="/test/db.sqlite",
            database_size="1.2 MB",
            watch_status=None,
            timestamp=datetime.now(),
        ),
        available_projects={"test-project": {"path": "/test/path"}},
    )

    # Mock the async project_info function
    with patch(
        "basic_memory.cli.commands.project.project_info.fn", new_callable=AsyncMock
    ) as mock_func:
        mock_func.return_value = mock_info

        # Run the command with --json flag
        result = runner.invoke(cli_app, ["project", "info", "--json"])

        # Verify exit code
        assert result.exit_code == 0

        # Parse JSON output
        output = json.loads(result.stdout)

        # Verify JSON structure matches our mock data
        assert output["default_project"] == "test-project"
        assert output["project_name"] == "test-project"
        assert output["statistics"]["total_entities"] == 10
