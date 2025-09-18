"""Tests for discussion context MCP tool."""

import pytest

from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import recent_activity
from basic_memory.schemas.memory import (
    EntitySummary,
    ObservationSummary,
    RelationSummary,
    GraphContext,
    ProjectActivitySummary,
    ProjectActivity,
)
from basic_memory.schemas.search import SearchItemType

# Test data for different timeframe formats
valid_timeframes = [
    "7d",  # Standard format
    "yesterday",  # Natural language
    "0d",  # Zero duration
]

invalid_timeframes = [
    "invalid",  # Nonsense string
    "tomorrow",  # Future date
]


@pytest.mark.asyncio
async def test_recent_activity_timeframe_formats(client, test_project, test_graph):
    """Test that recent_activity accepts various timeframe formats."""
    # Test each valid timeframe with project-specific mode
    for timeframe in valid_timeframes:
        try:
            result = await recent_activity.fn(
                project=test_project.name,
                type=["entity"], timeframe=timeframe, page=1, page_size=10, max_related=10
            )
            assert result is not None
            assert isinstance(result, GraphContext)
        except Exception as e:
            pytest.fail(f"Failed with valid timeframe '{timeframe}': {str(e)}")

    # Test invalid timeframes should raise ValidationError
    for timeframe in invalid_timeframes:
        with pytest.raises(ToolError):
            await recent_activity.fn(project=test_project.name, timeframe=timeframe)


@pytest.mark.asyncio
async def test_recent_activity_type_filters(client, test_project, test_graph):
    """Test that recent_activity correctly filters by types."""

    # Test single string type
    result = await recent_activity.fn(project=test_project.name, type=SearchItemType.ENTITY)
    assert result is not None
    assert isinstance(result, GraphContext)
    assert len(result.results) > 0
    assert all(isinstance(item.primary_result, EntitySummary) for item in result.results)

    # Test single string type
    result = await recent_activity.fn(project=test_project.name, type="entity")
    assert result is not None
    assert isinstance(result, GraphContext)
    assert len(result.results) > 0
    assert all(isinstance(item.primary_result, EntitySummary) for item in result.results)

    # Test single type
    result = await recent_activity.fn(project=test_project.name, type=["entity"])
    assert result is not None
    assert isinstance(result, GraphContext)
    assert len(result.results) > 0
    assert all(isinstance(item.primary_result, EntitySummary) for item in result.results)

    # Test multiple types
    result = await recent_activity.fn(project=test_project.name, type=["entity", "observation"])
    assert result is not None
    assert isinstance(result, GraphContext)
    assert len(result.results) > 0
    assert all(
        isinstance(item.primary_result, EntitySummary)
        or isinstance(item.primary_result, ObservationSummary)
        for item in result.results
    )

    # Test multiple types
    result = await recent_activity.fn(project=test_project.name, type=[SearchItemType.ENTITY, SearchItemType.OBSERVATION])
    assert result is not None
    assert isinstance(result, GraphContext)
    assert len(result.results) > 0
    assert all(
        isinstance(item.primary_result, EntitySummary)
        or isinstance(item.primary_result, ObservationSummary)
        for item in result.results
    )

    # Test all types
    result = await recent_activity.fn(project=test_project.name, type=["entity", "observation", "relation"])
    assert result is not None
    assert isinstance(result, GraphContext)
    assert len(result.results) > 0
    # Results can be any type
    assert all(
        isinstance(item.primary_result, EntitySummary)
        or isinstance(item.primary_result, ObservationSummary)
        or isinstance(item.primary_result, RelationSummary)
        for item in result.results
    )


@pytest.mark.asyncio
async def test_recent_activity_type_invalid(client, test_project, test_graph):
    """Test that recent_activity correctly filters by types."""

    # Test single invalid string type
    with pytest.raises(ValueError) as e:
        await recent_activity.fn(project=test_project.name, type="note")
    assert (
        str(e.value) == "Invalid type: note. Valid types are: ['entity', 'observation', 'relation']"
    )

    # Test invalid string array type
    with pytest.raises(ValueError) as e:
        await recent_activity.fn(project=test_project.name, type=["note"])
    assert (
        str(e.value) == "Invalid type: note. Valid types are: ['entity', 'observation', 'relation']"
    )


@pytest.mark.asyncio
async def test_recent_activity_discovery_mode(client, test_project, test_graph):
    """Test that recent_activity discovery mode works without project parameter."""
    # Test discovery mode (no project parameter)
    result = await recent_activity.fn()
    assert result is not None
    assert isinstance(result, ProjectActivitySummary)

    # Check that we get a summary with projects
    assert hasattr(result, 'projects')
    assert hasattr(result, 'summary')
    assert hasattr(result, 'timeframe')
    assert hasattr(result, 'generated_at')

    # Check summary stats
    assert result.summary.total_projects >= 1  # Should have at least the test project
    assert isinstance(result.summary.active_projects, int)
    assert isinstance(result.summary.total_items, int)

    # Check projects dictionary structure
    assert isinstance(result.projects, dict)

    # Each project should have proper activity structure
    for project_name, project_activity in result.projects.items():
        assert isinstance(project_activity, ProjectActivity)
        assert project_activity.project_name == project_name
        assert isinstance(project_activity.activity, GraphContext)
        assert isinstance(project_activity.item_count, int)
        assert isinstance(project_activity.active_folders, list)
