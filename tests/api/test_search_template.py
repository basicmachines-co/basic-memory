"""Tests for the search template rendering."""

import datetime
import pytest

from basic_memory.api.template_loader import TemplateLoader
from basic_memory.schemas.search import SearchItemType, SearchResult


@pytest.fixture
def template_loader():
    """Return a TemplateLoader instance for testing."""
    return TemplateLoader()


@pytest.fixture
def search_result():
    """Create a sample SearchResult for testing."""
    return SearchResult(
        title="Test Search Result",
        type=SearchItemType.ENTITY,
        permalink="test/search-result",
        score=0.95,
        content="This is a test search result with some content.",
        file_path="/path/to/test/search-result.md",
        metadata={"created_at": datetime.datetime(2023, 2, 1, 12, 0)},
    )


@pytest.fixture
def context_with_results(search_result):
    """Create a sample context with search results."""
    return {
        "query": "test query",
        "timeframe": "30d",
        "has_results": True,
        "result_count": 1,
        "results": [search_result],
    }


@pytest.fixture
def context_without_results():
    """Create a sample context without search results."""
    return {
        "query": "empty query",
        "timeframe": None,
        "has_results": False,
        "result_count": 0,
        "results": [],
    }


@pytest.mark.asyncio
async def test_search_with_results(template_loader, context_with_results):
    """Test rendering the search template with results."""
    result = await template_loader.render("prompts/search.hbs", context_with_results)

    # Check that key elements are present
    assert 'Search Results for: "test query" (after 30d)' in result
    assert "1.0. Test Search Result" in result
    assert "Type**: entity" in result
    assert "Relevance Score**: 0.95" in result
    assert "This is a test search result with some content." in result
    assert 'read_note("test/search-result")' in result
    assert "Next Steps" in result
    assert "Synthesize and Capture Knowledge" in result


@pytest.mark.asyncio
async def test_search_without_results(template_loader, context_without_results):
    """Test rendering the search template without results."""
    result = await template_loader.render("prompts/search.hbs", context_without_results)

    # Check that key elements are present
    assert 'Search Results for: "empty query"' in result
    assert "I couldn't find any results for this query." in result
    assert "Opportunity to Capture Knowledge!" in result
    assert "write_note(" in result
    assert 'title="Empty query"' in result
    assert "Other Suggestions" in result


@pytest.mark.asyncio
async def test_multiple_search_results(template_loader):
    """Test rendering the search template with multiple results."""
    # Create multiple search results
    results = []
    for i in range(1, 6):  # Create 5 results
        results.append(
            SearchResult(
                title=f"Search Result {i}",
                type=SearchItemType.ENTITY,
                permalink=f"test/result-{i}",
                score=1.0 - (i * 0.1),  # Decreasing scores
                content=f"Content for result {i}",
                file_path=f"/path/to/result-{i}.md",
                metadata={},
            )
        )

    context = {
        "query": "multiple results",
        "timeframe": None,
        "has_results": True,
        "result_count": len(results),
        "results": results,
    }

    result = await template_loader.render("prompts/search.hbs", context)

    # Check that all results are rendered
    for i in range(1, 6):
        assert f"{i}.0. Search Result {i}" in result
        assert f"Content for result {i}" in result
        assert f'read_note("test/result-{i}")' in result


@pytest.mark.asyncio
async def test_capitalization_in_write_note_template(template_loader, context_with_results):
    """Test that the query is capitalized in the write_note template."""
    result = await template_loader.render("prompts/search.hbs", context_with_results)

    # The query should be capitalized in the suggested write_note call
    assert "Synthesis of Test query Information" in result


@pytest.mark.asyncio
async def test_timeframe_display(template_loader):
    """Test that the timeframe is displayed correctly when present, and not when absent."""
    # Context with timeframe
    context_with_timeframe = {
        "query": "with timeframe",
        "timeframe": "7d",
        "has_results": True,
        "result_count": 0,
        "results": [],
    }

    result_with_timeframe = await template_loader.render(
        "prompts/search.hbs", context_with_timeframe
    )
    assert 'Search Results for: "with timeframe" (after 7d)' in result_with_timeframe

    # Context without timeframe
    context_without_timeframe = {
        "query": "without timeframe",
        "timeframe": None,
        "has_results": True,
        "result_count": 0,
        "results": [],
    }

    result_without_timeframe = await template_loader.render(
        "prompts/search.hbs", context_without_timeframe
    )
    assert 'Search Results for: "without timeframe"' in result_without_timeframe
    assert 'Search Results for: "without timeframe" (after' not in result_without_timeframe
