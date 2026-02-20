"""Tests for discussion context MCP tool."""

import pytest

from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import build_context


@pytest.mark.asyncio
async def test_get_basic_discussion_context(client, test_graph, test_project):
    """Test getting basic discussion context returns slimmed JSON dict."""
    result = await build_context.fn(project=test_project.name, url="memory://test/root")

    assert isinstance(result, dict)
    assert len(result["results"]) == 1

    primary = result["results"][0]["primary_result"]
    assert primary["permalink"] == f"{test_project.name}/test/root"
    assert len(result["results"][0]["related_results"]) > 0

    # Verify metadata — stripped fields should be absent
    meta = result["metadata"]
    assert meta["uri"] == f"{test_project.name}/test/root"
    assert meta["depth"] == 1  # default depth
    assert meta["timeframe"] is not None
    assert meta["primary_count"] == 1
    assert "generated_at" not in meta
    assert "total_results" not in meta

    # Verify entity-level stripped fields
    assert "entity_id" not in primary
    assert "created_at" not in primary

    # Verify observation-level stripped fields
    if result["results"][0]["observations"]:
        obs = result["results"][0]["observations"][0]
        assert "observation_id" not in obs
        assert "entity_id" not in obs
        assert "file_path" not in obs


@pytest.mark.asyncio
async def test_get_discussion_context_pattern(client, test_graph, test_project):
    """Test getting context with pattern matching."""
    result = await build_context.fn(project=test_project.name, url="memory://test/*", depth=1)

    assert isinstance(result, dict)
    assert len(result["results"]) > 1  # Should match multiple test/* paths
    assert all(
        f"{test_project.name}/test/" in item["primary_result"]["permalink"]
        for item in result["results"]
    )
    assert result["metadata"]["depth"] == 1


@pytest.mark.asyncio
async def test_get_discussion_context_timeframe(client, test_graph, test_project):
    """Test timeframe parameter filtering."""
    # Get recent context
    recent = await build_context.fn(
        project=test_project.name,
        url="memory://test/root",
        timeframe="1d",
    )

    # Get older context
    older = await build_context.fn(
        project=test_project.name,
        url="memory://test/root",
        timeframe="30d",
    )

    # Calculate total related items
    total_recent_related = (
        sum(len(item["related_results"]) for item in recent["results"]) if recent["results"] else 0
    )
    total_older_related = (
        sum(len(item["related_results"]) for item in older["results"]) if older["results"] else 0
    )

    assert total_older_related >= total_recent_related


@pytest.mark.asyncio
async def test_get_discussion_context_not_found(client, test_project):
    """Test handling of non-existent URIs."""
    result = await build_context.fn(project=test_project.name, url="memory://test/does-not-exist")

    assert isinstance(result, dict)
    assert len(result["results"]) == 0
    assert result["metadata"]["primary_count"] == 0
    assert result["metadata"]["related_count"] == 0


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
async def test_build_context_timeframe_formats(client, test_graph, test_project):
    """Test that build_context accepts various timeframe formats."""
    test_url = "memory://specs/test"

    # Test each valid timeframe
    for timeframe in valid_timeframes:
        try:
            result = await build_context.fn(
                project=test_project.name,
                url=test_url,
                timeframe=timeframe,
                page=1,
                page_size=10,
                max_related=10,
            )
            assert result is not None
        except Exception as e:
            pytest.fail(f"Failed with valid timeframe '{timeframe}': {str(e)}")

    # Test invalid timeframes should raise ValidationError
    for timeframe in invalid_timeframes:
        with pytest.raises(ToolError):
            await build_context.fn(project=test_project.name, url=test_url, timeframe=timeframe)


@pytest.mark.asyncio
async def test_build_context_string_depth_parameter(client, test_graph, test_project):
    """Test that build_context handles string depth parameter correctly."""
    test_url = "memory://test/root"

    # Test valid string depth parameter — should convert to int
    try:
        result = await build_context.fn(url=test_url, depth="2", project=test_project.name)
        assert isinstance(result["metadata"]["depth"], int)
        assert result["metadata"]["depth"] == 2
    except ToolError:
        # This is also acceptable behavior - type validation should catch it
        pass

    # Test invalid string depth parameter - should raise ToolError
    with pytest.raises(ToolError):
        await build_context.fn(test_url, depth="invalid", project=test_project.name)


@pytest.mark.asyncio
async def test_build_context_text_format(client, test_graph, test_project):
    """Test that output_format='text' returns compact text."""
    result = await build_context.fn(
        project=test_project.name,
        url="memory://test/root",
        output_format="text",
    )

    assert isinstance(result, str)
    # Should contain the context header
    assert "# Context:" in result
    # Should contain the entity title
    assert "Root" in result
    # Should contain the footer with counts
    assert "primary" in result
    assert "project:" in result


@pytest.mark.asyncio
async def test_build_context_markdown_pattern(client, test_graph, test_project):
    """Test markdown format with pattern matching (multiple results)."""
    result = await build_context.fn(
        project=test_project.name,
        url="memory://test/*",
        output_format="text",
    )

    assert isinstance(result, str)
    # Multiple results should use URI as title, not single entity title
    assert "# Context:" in result
    # Should contain separator between entity blocks
    assert "---" in result
    assert "primary" in result


@pytest.mark.asyncio
async def test_build_context_markdown_not_found(client, test_project):
    """Test markdown format for non-existent URIs."""
    result = await build_context.fn(
        project=test_project.name,
        url="memory://test/does-not-exist",
        output_format="text",
    )

    assert isinstance(result, str)
    assert "No results found" in result
    assert test_project.name in result
