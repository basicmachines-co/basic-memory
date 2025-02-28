"""Tests for discussion context MCP tool."""

import pytest
from datetime import datetime

from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import build_context, recent_activity
from basic_memory.schemas.memory import (
    GraphContext,
    EntitySummary,
    ObservationSummary,
    RelationSummary,
)


@pytest.mark.asyncio
async def test_get_basic_discussion_context(client, test_graph):
    """Test getting basic discussion context."""
    context = await build_context(url="memory://test/root")

    assert isinstance(context, GraphContext)
    assert len(context.primary_results) == 1
    assert context.primary_results[0].permalink == "test/root"
    assert len(context.related_results) > 0

    # Verify metadata
    assert context.metadata.uri == "test/root"
    assert context.metadata.depth == 1  # default depth
    assert context.metadata.timeframe is not None
    assert isinstance(context.metadata.generated_at, datetime)


@pytest.mark.asyncio
async def test_get_discussion_context_pattern(client, test_graph):
    """Test getting context with pattern matching."""
    context = await build_context(url="memory://test/*", depth=1)

    assert isinstance(context, GraphContext)
    assert len(context.primary_results) > 1  # Should match multiple test/* paths
    assert all("test/" in e.permalink for e in context.primary_results)
    assert context.metadata.depth == 1


@pytest.mark.asyncio
async def test_get_discussion_context_timeframe(client, test_graph):
    """Test timeframe parameter filtering."""
    # Get recent context
    recent_context = await build_context(
        url="memory://test/root",
        timeframe="1d",  # Last 24 hours
    )

    # Get older context
    older_context = await build_context(
        url="memory://test/root",
        timeframe="30d",  # Last 30 days
    )

    assert len(older_context.related_results) >= len(recent_context.related_results)


@pytest.mark.asyncio
async def test_get_discussion_context_not_found(client):
    """Test handling of non-existent URIs."""
    context = await build_context(url="memory://test/does-not-exist")

    assert isinstance(context, GraphContext)
    assert len(context.primary_results) == 0
    assert len(context.related_results) == 0


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
    # Test single type
    result = await recent_activity(type=["entity"])
    assert result is not None
    assert all(isinstance(r, EntitySummary) for r in result.primary_results)

    # Test multiple types
    result = await recent_activity(type=["entity", "observation"])
    assert result is not None
    assert all(
        isinstance(r, EntitySummary) or isinstance(r, ObservationSummary)
        for r in result.primary_results
    )

    # Test all types
    result = await recent_activity(type=["entity", "observation", "relation"])
    assert result is not None
    # Results can be any type
    assert all(
        isinstance(r, EntitySummary)
        or isinstance(r, ObservationSummary)
        or isinstance(r, RelationSummary)
        for r in result.primary_results
    )


@pytest.mark.asyncio
async def test_build_context_timeframe_formats(client, test_graph):
    """Test that build_context accepts various timeframe formats."""
    test_url = "memory://specs/test"

    # Test each valid timeframe
    for timeframe in valid_timeframes:
        try:
            result = await build_context(
                url=test_url, timeframe=timeframe, page=1, page_size=10, max_related=10
            )
            assert result is not None
        except Exception as e:
            pytest.fail(f"Failed with valid timeframe '{timeframe}': {str(e)}")

    # Test invalid timeframes should raise ValidationError
    for timeframe in invalid_timeframes:
        with pytest.raises(ToolError):
            await build_context(url=test_url, timeframe=timeframe)
