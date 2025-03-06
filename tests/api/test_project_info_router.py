"""Tests for the stats router API endpoints."""

import json
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_get_project_info_endpoint(test_graph, client, test_config):
    """Test the project-info endpoint returns correctly structured data."""
    # Set up some test data in the database

    # Call the endpoint
    response = await client.get("/stats/project-info")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check top-level keys
    assert "project_name" in data
    assert "project_path" in data
    assert "available_projects" in data
    assert "default_project" in data
    assert "statistics" in data
    assert "activity" in data
    assert "system" in data

    # Check statistics
    stats = data["statistics"]
    assert "total_entities" in stats
    assert stats["total_entities"] >= 0
    assert "total_observations" in stats
    assert stats["total_observations"] >= 0
    assert "total_relations" in stats
    assert stats["total_relations"] >= 0

    # Check activity
    activity = data["activity"]
    assert "recently_created" in activity
    assert "recently_updated" in activity
    assert "monthly_growth" in activity

    # Check system
    system = data["system"]
    assert "version" in system
    assert "database_path" in system
    assert "database_size" in system
    assert "timestamp" in system


@pytest.mark.asyncio
async def test_get_project_info_content(test_graph, client, test_config):
    """Test that project-info contains actual data from the test database."""
    # Call the endpoint
    response = await client.get("/stats/project-info")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Check that test_graph content is reflected in statistics
    stats = data["statistics"]

    # Our test graph should have at least a few entities
    assert stats["total_entities"] > 0

    # It should also have some observations
    assert stats["total_observations"] > 0

    # And relations
    assert stats["total_relations"] > 0

    # Check that entity types include 'test'
    assert "test" in stats["entity_types"] or "entity" in stats["entity_types"]


@pytest.mark.asyncio
async def test_get_project_info_watch_status(test_graph, client, test_config):
    """Test that project-info correctly handles watch status."""
    # Create a mock watch status file
    mock_watch_status = {
        "running": True,
        "start_time": "2025-03-05T18:00:42.752435",
        "pid": 7321,
        "error_count": 0,
        "last_error": None,
        "last_scan": "2025-03-05T19:59:02.444416",
        "synced_files": 6,
        "recent_events": [],
    }

    # Mock the Path.exists and Path.read_text methods
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=json.dumps(mock_watch_status)),
    ):
        # Call the endpoint
        response = await client.get("/stats/project-info")

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Check that watch status is included
        assert data["system"]["watch_status"] is not None
        assert data["system"]["watch_status"]["running"] is True
        assert data["system"]["watch_status"]["pid"] == 7321
        assert data["system"]["watch_status"]["synced_files"] == 6
