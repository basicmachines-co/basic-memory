"""Tests for memory router endpoints."""

from datetime import datetime

import pytest

from basic_memory.schemas.memory import GraphContext


@pytest.mark.asyncio
async def test_get_memory_context(client, test_graph, project_url):
    """Test getting context from memory URL."""
    response = await client.get(f"{project_url}/memory/test/root")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) == 1
    assert context.results[0].primary_result.permalink == "test/root"
    assert len(context.results[0].related_results) > 0

    # Verify metadata
    assert context.metadata.uri == "test/root"
    assert context.metadata.depth == 1  # default depth
    assert isinstance(context.metadata.generated_at, datetime)
    assert context.metadata.primary_count + context.metadata.related_count > 0
    assert context.metadata.total_results is not None  # Backwards compatibility field


@pytest.mark.asyncio
async def test_get_memory_context_pagination(client, test_graph, project_url):
    """Test getting context from memory URL."""
    response = await client.get(f"{project_url}/memory/test/root?page=1&page_size=1")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) == 1
    assert context.results[0].primary_result.permalink == "test/root"
    assert len(context.results[0].related_results) > 0

    # Verify metadata
    assert context.metadata.uri == "test/root"
    assert context.metadata.depth == 1  # default depth
    assert isinstance(context.metadata.generated_at, datetime)
    assert context.metadata.primary_count > 0


@pytest.mark.asyncio
async def test_get_memory_context_pattern(client, test_graph, project_url):
    """Test getting context with pattern matching."""
    response = await client.get(f"{project_url}/memory/test/*")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) > 1  # Should match multiple test/* paths
    assert all("test/" in item.primary_result.permalink for item in context.results)


@pytest.mark.asyncio
async def test_get_memory_context_depth(client, test_graph, project_url):
    """Test depth parameter affects relation traversal."""
    # With depth=1, should only get immediate connections
    response = await client.get(f"{project_url}/memory/test/root?depth=1&max_results=20")
    assert response.status_code == 200
    context1 = GraphContext(**response.json())

    # With depth=2, should get deeper connections
    response = await client.get(f"{project_url}/memory/test/root?depth=3&max_results=20")
    assert response.status_code == 200
    context2 = GraphContext(**response.json())

    # Calculate total related items in all result items
    total_related1 = sum(len(item.related_results) for item in context1.results)
    total_related2 = sum(len(item.related_results) for item in context2.results)

    assert total_related2 > total_related1


@pytest.mark.asyncio
async def test_get_memory_context_timeframe(client, test_graph, project_url):
    """Test timeframe parameter filters by date."""
    # Recent timeframe
    response = await client.get(f"{project_url}/memory/test/root?timeframe=1d")
    assert response.status_code == 200
    recent = GraphContext(**response.json())

    # Longer timeframe
    response = await client.get(f"{project_url}/memory/test/root?timeframe=30d")
    assert response.status_code == 200
    older = GraphContext(**response.json())

    # Calculate total related items
    total_recent_related = (
        sum(len(item.related_results) for item in recent.results) if recent.results else 0
    )
    total_older_related = (
        sum(len(item.related_results) for item in older.results) if older.results else 0
    )

    assert total_older_related >= total_recent_related


@pytest.mark.asyncio
async def test_not_found(client, project_url):
    """Test handling of non-existent paths."""
    response = await client.get(f"{project_url}/memory/test/does-not-exist")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) == 0


@pytest.mark.asyncio
async def test_recent_activity(client, test_graph, project_url):
    """Test handling of recent activity."""
    response = await client.get(f"{project_url}/memory/recent")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) > 0
    assert context.metadata.primary_count > 0


@pytest.mark.asyncio
async def test_recent_activity_pagination(client, test_graph, project_url):
    """Test pagination for recent activity."""
    response = await client.get(f"{project_url}/memory/recent?page=1&page_size=1")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) == 1
    assert context.page == 1
    assert context.page_size == 1


@pytest.mark.asyncio
async def test_recent_activity_by_type(client, test_graph, project_url):
    """Test filtering recent activity by type."""
    response = await client.get(f"{project_url}/memory/recent?type=relation&type=observation")
    assert response.status_code == 200

    context = GraphContext(**response.json())
    assert len(context.results) > 0

    # Check for relation and observation types in primary results
    primary_types = [item.primary_result.type for item in context.results]
    assert "relation" in primary_types or "observation" in primary_types
