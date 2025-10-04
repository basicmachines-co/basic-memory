"""Tests for discussion context MCP tool."""

import pytest

from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import recent_activity
from basic_memory.schemas.search import SearchItemType

# Test data for different timeframe formats
valid_timeframes = [
    "7d",  # Standard format
    "yesterday",  # Natural language
    "0d",  # Zero duration
]

invalid_timeframes = [
    "invalid",  # Nonsense string
    # NOTE: "tomorrow" now returns 1 day ago due to timezone safety - no longer invalid
]


@pytest.mark.asyncio
async def test_recent_activity_timeframe_formats(client, test_project, test_graph):
    """Test that recent_activity accepts various timeframe formats."""
    # Test each valid timeframe with project-specific mode
    for timeframe in valid_timeframes:
        try:
            result = await recent_activity.fn(
                project=test_project.name,
                type=["entity"],
                timeframe=timeframe,
            )
            assert result is not None
            assert isinstance(result, str)
            assert "Recent Activity:" in result
            assert timeframe in result
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
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Recent Notes & Documents" in result

    # Test single string type
    result = await recent_activity.fn(project=test_project.name, type="entity")
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Recent Notes & Documents" in result

    # Test single type
    result = await recent_activity.fn(project=test_project.name, type=["entity"])
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Recent Notes & Documents" in result

    # Test multiple types
    result = await recent_activity.fn(project=test_project.name, type=["entity", "observation"])
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    # Should contain sections for both types
    assert "Recent Notes & Documents" in result or "Recent Observations" in result

    # Test multiple types
    result = await recent_activity.fn(
        project=test_project.name, type=[SearchItemType.ENTITY, SearchItemType.OBSERVATION]
    )
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    # Should contain sections for both types
    assert "Recent Notes & Documents" in result or "Recent Observations" in result

    # Test all types
    result = await recent_activity.fn(
        project=test_project.name, type=["entity", "observation", "relation"]
    )
    assert result is not None
    assert isinstance(result, str)
    assert "Recent Activity:" in result
    assert "Activity Summary:" in result


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
    assert isinstance(result, str)

    # Check that we get a formatted summary
    assert "Recent Activity Summary" in result
    assert "Most Active Project:" in result or "Other Active Projects:" in result
    assert "Summary:" in result
    assert "active projects" in result

    # Should contain project discovery guidance
    assert "Suggested project:" in result or "Multiple active projects" in result
    assert "Session reminder:" in result
