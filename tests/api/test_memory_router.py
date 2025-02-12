"""Tests for memory router endpoints."""

from datetime import datetime

import pytest

from basic_memory.schemas.memory import GraphContext, RelationSummary, ObservationSummary


@pytest.mark.asyncio
async def test_get_memory_context(client, test_graph):
    """Test getting context from memory URL."""
    response = await client.get("/memory/test/root")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.primary_results) == 1
    assert context.primary_results[0].permalink == "test/root"
    assert len(context.related_results) > 0

    # Verify metadata
    assert context.metadata.uri == "test/root"
    assert context.metadata.depth == 1  # default depth
    # assert context.metadata["timeframe"] == "7d"  # default timeframe
    assert isinstance(context.metadata.generated_at, datetime)
    assert context.metadata.total_results == 3


@pytest.mark.asyncio
async def test_get_memory_context_pattern(client, test_graph):
    """Test getting context with pattern matching."""
    response = await client.get("/memory/test/*")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.primary_results) > 1  # Should match multiple test/* paths
    assert all("test/" in e.permalink for e in context.primary_results)


@pytest.mark.asyncio
async def test_get_memory_context_depth(client, test_graph):
    """Test depth parameter affects relation traversal."""
    # With depth=1, should only get immediate connections
    response = await client.get("/memory/test/root?depth=1&max_results=20")
    assert response.status_code == 200
    context1 = GraphContext(**response.json())

    # With depth=2, should get deeper connections
    response = await client.get("/memory/test/root?depth=3&max_results=20")
    assert response.status_code == 200
    context2 = GraphContext(**response.json())

    assert len(context2.related_results) > len(context1.related_results)


@pytest.mark.asyncio
async def test_get_memory_context_timeframe(client, test_graph):
    """Test timeframe parameter filters by date."""
    # Recent timeframe
    response = await client.get("/memory/test/root?timeframe=1d")
    assert response.status_code == 200
    recent = GraphContext(**response.json())

    # Longer timeframe
    response = await client.get("/memory/test/root?timeframe=30d")
    assert response.status_code == 200
    older = GraphContext(**response.json())

    assert len(older.related_results) >= len(recent.related_results)


@pytest.mark.asyncio
async def test_get_related_context_filters(client, test_graph):
    """Test filtering related content by relation type."""
    response = await client.get("/memory/related/test/root")
    assert response.status_code == 200

    context = GraphContext(**response.json())


@pytest.mark.asyncio
async def test_not_found(client):
    """Test handling of non-existent paths."""
    response = await client.get("/memory/test/does-not-exist")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.primary_results) == 0
    assert len(context.related_results) == 0


@pytest.mark.asyncio
async def test_recent_activity(client, test_graph):
    """Test handling of non-existent paths."""
    response = await client.get("/memory/recent")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.primary_results) > 0
    assert len(context.related_results) > 0


@pytest.mark.asyncio
async def test_recent_activity_by_type(client, test_graph):
    """Test handling of non-existent paths."""
    response = await client.get("/memory/recent?type=relation&type=observation")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.primary_results) > 0

    for r in context.primary_results:
        assert isinstance(r, RelationSummary | ObservationSummary)

    assert len(context.related_results) > 0
