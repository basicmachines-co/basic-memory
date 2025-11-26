"""Tests for the continue_conversation template rendering."""

import datetime
import pytest

from basic_memory.api.template_loader import TemplateLoader
from basic_memory.schemas.memory import EntitySummary
from basic_memory.schemas.search import SearchItemType


@pytest.fixture
def template_loader():
    """Return a TemplateLoader instance for testing."""
    return TemplateLoader()


@pytest.fixture
def entity_summary():
    """Create a sample EntitySummary for testing."""
    return EntitySummary(
        entity_id=1,
        title="Test Entity",
        permalink="test/entity",
        type=SearchItemType.ENTITY,
        content="This is a test entity with some content.",
        file_path="/path/to/test/entity.md",
        created_at=datetime.datetime(2023, 1, 1, 12, 0),
    )


@pytest.fixture
def context_with_results(entity_summary):
    """Create a sample context with results for testing."""
    from basic_memory.schemas.memory import ObservationSummary, ContextResult

    # Create an observation for the entity
    observation = ObservationSummary(
        observation_id=1,
        entity_id=1,
        title="Test Observation",
        permalink="test/entity/observations/1",
        category="test",
        content="This is a test observation.",
        file_path="/path/to/test/entity.md",
        created_at=datetime.datetime(2023, 1, 1, 12, 0),
    )

    # Create a context result with primary_result, observations, and related_results
    context_item = ContextResult(
        primary_result=entity_summary,
        observations=[observation],
        related_results=[entity_summary],
    )

    return {
        "topic": "Test Topic",
        "timeframe": "7d",
        "has_results": True,
        "hierarchical_results": [context_item],
    }


@pytest.fixture
def context_without_results():
    """Create a sample context without results for testing."""
    return {
        "topic": "Empty Topic",
        "timeframe": "1d",
        "has_results": False,
        "hierarchical_results": [],
    }


@pytest.mark.asyncio
async def test_continue_conversation_with_results(template_loader, context_with_results):
    """Test rendering the continue_conversation template with results."""
    result = await template_loader.render("prompts/continue_conversation.hbs", context_with_results)

    # Check that key elements are present
    assert "Continuing conversation on: Test Topic" in result
    assert "memory://test/entity" in result
    assert "Test Entity" in result
    assert "This is a test entity with some content." in result
    assert "Related Context" in result
    assert "read_note" in result
    assert "Next Steps" in result
    assert "Knowledge Capture Recommendation" in result


@pytest.mark.asyncio
async def test_continue_conversation_without_results(template_loader, context_without_results):
    """Test rendering the continue_conversation template without results."""
    result = await template_loader.render(
        "prompts/continue_conversation.hbs", context_without_results
    )

    # Check that key elements are present
    assert "Continuing conversation on: Empty Topic" in result
    assert "The supplied query did not return any information" in result
    assert "Opportunity to Capture New Knowledge!" in result
    assert 'title="Empty Topic"' in result
    assert "Next Steps" in result
    assert "Knowledge Capture Recommendation" in result


@pytest.mark.asyncio
async def test_next_steps_section(template_loader, context_with_results):
    """Test that the next steps section is rendered correctly."""
    result = await template_loader.render("prompts/continue_conversation.hbs", context_with_results)

    assert "Next Steps" in result
    assert 'Explore more with: `search_notes("Test Topic")`' in result
    assert (
        f'See what\'s changed: `recent_activity(timeframe="{context_with_results["timeframe"]}")`'
        in result
    )
    assert "Record new learnings or decisions from this conversation" in result


@pytest.mark.asyncio
async def test_knowledge_capture_recommendation(template_loader, context_with_results):
    """Test that the knowledge capture recommendation is rendered."""
    result = await template_loader.render("prompts/continue_conversation.hbs", context_with_results)

    assert "Knowledge Capture Recommendation" in result
    assert "actively look for opportunities to:" in result
    assert "Record key information, decisions, or insights" in result
    assert "Link new knowledge to existing topics" in result
    assert "Suggest capturing important context" in result
    assert "one of the most valuable aspects of Basic Memory" in result


@pytest.mark.asyncio
async def test_timeframe_default_value(template_loader, context_with_results):
    """Test that the timeframe uses the default value when not provided."""
    # Remove the timeframe from the context
    context_without_timeframe = context_with_results.copy()
    context_without_timeframe["timeframe"] = None

    result = await template_loader.render(
        "prompts/continue_conversation.hbs", context_without_timeframe
    )

    # Check that the default value is used
    assert 'recent_activity(timeframe="7d")' in result
