"""Tests for the project_info MCP tool."""

from unittest.mock import patch, MagicMock

import pytest
from httpx import Response

from basic_memory.mcp.resources.project_info import project_info
from basic_memory.schemas import (
    ProjectInfoResponse,
)


@pytest.mark.asyncio
async def test_project_info_tool():
    """Test that the project_info tool calls the API and returns structured data."""
    # Create a mock response
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200

    # Create sample data that matches the schema
    sample_data = {
        "project_name": "test",
        "project_path": "/path/to/test",
        "available_projects": {
            "test": {
                "path": "/path/to/test",
                "active": True,
                "id": 1,
                "is_default": True,
                "permalink": "test",
            },
            "other": {
                "path": "/path/to/other",
                "active": False,
                "id": 2,
                "is_default": False,
                "permalink": "other",
            },
        },
        "default_project": "test",
        "statistics": {
            "total_entities": 42,
            "total_observations": 24,
            "total_relations": 18,
            "total_unresolved_relations": 3,
            "entity_types": {"note": 30, "conversation": 12},
            "observation_categories": {"tech": 15, "note": 9},
            "relation_types": {"relates_to": 10, "implements": 8},
            "most_connected_entities": [
                {"id": 1, "title": "Test Entity", "permalink": "test/entity", "relation_count": 5}
            ],
            "isolated_entities": 2,
        },
        "activity": {
            "recently_created": [
                {
                    "id": 1,
                    "title": "Test Entity",
                    "permalink": "test/entity",
                    "entity_type": "note",
                    "created_at": "2025-03-05T12:00:00",
                }
            ],
            "recently_updated": [
                {
                    "id": 1,
                    "title": "Test Entity",
                    "permalink": "test/entity",
                    "entity_type": "note",
                    "updated_at": "2025-03-05T12:00:00",
                }
            ],
            "monthly_growth": {
                "2025-03": {"entities": 10, "observations": 5, "relations": 8, "total": 23}
            },
        },
        "system": {
            "version": "0.1.0",
            "database_path": "/path/to/db.sqlite",
            "database_size": "2.50 MB",
            "watch_status": {
                "running": True,
                "start_time": "2025-03-05T12:00:00",
                "pid": 1234,
                "error_count": 0,
                "synced_files": 42,
            },
            "timestamp": "2025-03-05T12:00:00",
        },
    }

    mock_response.json.return_value = sample_data

    # Mock the call_get function
    with patch(
        "basic_memory.mcp.resources.project_info.call_get", return_value=mock_response
    ) as mock_call_get:
        # Call the function
        result = await project_info()

        # Verify that call_get was called with the correct URL
        mock_call_get.assert_called_once()
        args, kwargs = mock_call_get.call_args
        assert args[1] == "/test-project/project/info"

        # Verify the result is a ProjectInfoResponse
        assert isinstance(result, ProjectInfoResponse)

        # Verify the content
        assert result.project_name == "test"
        assert result.project_path == "/path/to/test"
        assert "test" in result.available_projects
        assert result.default_project == "test"

        # Check statistics
        assert result.statistics.total_entities == 42
        assert result.statistics.total_observations == 24
        assert result.statistics.total_relations == 18

        # Check activity
        assert len(result.activity.recently_created) == 1
        assert result.activity.recently_created[0]["title"] == "Test Entity"

        # Check system
        assert result.system.version == "0.1.0"
        assert result.system.database_size == "2.50 MB"
        assert result.system.watch_status is not None
        assert result.system.watch_status["running"] is True


@pytest.mark.asyncio
async def test_project_info_error_handling():
    """Test that the project_info tool handles errors gracefully."""
    # Mock call_get to raise an exception
    with patch(
        "basic_memory.mcp.resources.project_info.call_get", side_effect=Exception("Test error")
    ):
        # Verify that the exception propagates
        with pytest.raises(Exception) as excinfo:
            await project_info()

        # Verify error message
        assert "Test error" in str(excinfo.value)
