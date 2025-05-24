"""Tests for discussion context MCP tool."""

import pytest

from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import recent_activity
from basic_memory.schemas.memory import (
    EntitySummary,
    ObservationSummary,
    RelationSummary,
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
async def test_recent_activity_timeframe_formats(client, test_graph):
    """Test that recent_activity accepts various timeframe formats."""
    # Test each valid timeframe
    for timeframe in valid_timeframes:
        try:
            result = await recent_activity(
                type=["entity"], timeframe=timeframe, page=1, page_size=10, max_related=10
            )
            assert result is not None
        except Exception as e:
            pytest.fail(f"Failed with valid timeframe '{timeframe}': {str(e)}")

    # Test invalid timeframes should raise ValidationError
    for timeframe in invalid_timeframes:
        with pytest.raises(ToolError):
            await recent_activity(timeframe=timeframe)


@pytest.mark.asyncio
async def test_recent_activity_type_filters(client, test_graph):
    """Test that recent_activity correctly filters by types."""

    # Test single string type
    result = await recent_activity(type=SearchItemType.ENTITY)
    assert result is not None
    assert len(result.results) > 0
    assert all(isinstance(item.primary_result, EntitySummary) for item in result.results)

    # Test single string type
    result = await recent_activity(type="entity")
    assert result is not None
    assert len(result.results) > 0
    assert all(isinstance(item.primary_result, EntitySummary) for item in result.results)

    # Test single type
    result = await recent_activity(type=["entity"])
    assert result is not None
    assert len(result.results) > 0
    assert all(isinstance(item.primary_result, EntitySummary) for item in result.results)

    # Test multiple types
    result = await recent_activity(type=["entity", "observation"])
    assert result is not None
    assert len(result.results) > 0
    assert all(
        isinstance(item.primary_result, EntitySummary)
        or isinstance(item.primary_result, ObservationSummary)
        for item in result.results
    )

    # Test multiple types
    result = await recent_activity(type=[SearchItemType.ENTITY, SearchItemType.OBSERVATION])
    assert result is not None
    assert len(result.results) > 0
    assert all(
        isinstance(item.primary_result, EntitySummary)
        or isinstance(item.primary_result, ObservationSummary)
        for item in result.results
    )

    # Test all types
    result = await recent_activity(type=["entity", "observation", "relation"])
    assert result is not None
    assert len(result.results) > 0
    # Results can be any type
    assert all(
        isinstance(item.primary_result, EntitySummary)
        or isinstance(item.primary_result, ObservationSummary)
        or isinstance(item.primary_result, RelationSummary)
        for item in result.results
    )


@pytest.mark.asyncio
async def test_recent_activity_type_invalid(client, test_graph):
    """Test that recent_activity correctly filters by types."""

    # Test single invalid string type
    with pytest.raises(ValueError) as e:
        await recent_activity(type="note")
    assert (
        str(e.value) == "Invalid type: note. Valid types are: ['entity', 'observation', 'relation']"
    )

    # Test invalid string array type
    with pytest.raises(ValueError) as e:
        await recent_activity(type=["note"])
    assert (
        str(e.value) == "Invalid type: note. Valid types are: ['entity', 'observation', 'relation']"
    )
