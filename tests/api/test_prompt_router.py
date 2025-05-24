"""Tests for the prompt router endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from basic_memory.services.context_service import ContextService


@pytest_asyncio.fixture
async def context_service(entity_repository, search_service, observation_repository):
    """Create a real context service for testing."""
    return ContextService(entity_repository, search_service, observation_repository)


@pytest.mark.asyncio
async def test_continue_conversation_endpoint(
    client: AsyncClient,
    entity_service,
    search_service,
    context_service,
    entity_repository,
    test_graph,
    project_url,
):
    """Test the continue_conversation endpoint with real services."""
    # Create request data
    request_data = {
        "topic": "Root",  # This should match our test entity in test_graph
        "timeframe": "7d",
        "depth": 1,
        "related_items_limit": 2,
    }

    # Call the endpoint
    response = await client.post(f"{project_url}/prompt/continue-conversation", json=request_data)

    # Verify response
    assert response.status_code == 200
    result = response.json()
    assert "prompt" in result
    assert "context" in result

    # Check content of context
    context = result["context"]
    assert context["topic"] == "Root"
    assert context["timeframe"] == "7d"
    assert context["has_results"] is True
    assert len(context["hierarchical_results"]) > 0

    # Check content of prompt
    prompt = result["prompt"]
    assert "Continuing conversation on: Root" in prompt
    assert "memory retrieval session" in prompt

    # Test without topic - should use recent activity
    request_data = {"timeframe": "1d", "depth": 1, "related_items_limit": 2}

    response = await client.post(f"{project_url}/prompt/continue-conversation", json=request_data)

    assert response.status_code == 200
    result = response.json()
    assert "Recent Activity" in result["context"]["topic"]


@pytest.mark.asyncio
async def test_search_prompt_endpoint(
    client: AsyncClient, entity_service, search_service, test_graph, project_url
):
    """Test the search_prompt endpoint with real services."""
    # Create request data
    request_data = {
        "query": "Root",  # This should match our test entity
        "timeframe": "7d",
    }

    # Call the endpoint
    response = await client.post(f"{project_url}/prompt/search", json=request_data)

    # Verify response
    assert response.status_code == 200
    result = response.json()
    assert "prompt" in result
    assert "context" in result

    # Check content of context
    context = result["context"]
    assert context["query"] == "Root"
    assert context["timeframe"] == "7d"
    assert context["has_results"] is True
    assert len(context["results"]) > 0

    # Check content of prompt
    prompt = result["prompt"]
    assert 'Search Results for: "Root"' in prompt
    assert "This is a memory search session" in prompt


@pytest.mark.asyncio
async def test_search_prompt_no_results(
    client: AsyncClient, entity_service, search_service, project_url
):
    """Test the search_prompt endpoint with a query that returns no results."""
    # Create request data with a query that shouldn't match anything
    request_data = {"query": "NonExistentQuery12345", "timeframe": "7d"}

    # Call the endpoint
    response = await client.post(f"{project_url}/prompt/search", json=request_data)

    # Verify response
    assert response.status_code == 200
    result = response.json()

    # Check content of context
    context = result["context"]
    assert context["query"] == "NonExistentQuery12345"
    assert context["has_results"] is False
    assert len(context["results"]) == 0

    # Check content of prompt
    prompt = result["prompt"]
    assert 'Search Results for: "NonExistentQuery12345"' in prompt
    assert "I couldn't find any results for this query" in prompt
    assert "Opportunity to Capture Knowledge" in prompt


@pytest.mark.asyncio
async def test_error_handling(client: AsyncClient, monkeypatch, project_url):
    """Test error handling in the endpoints by breaking the template loader."""

    # Patch the template loader to raise an exception
    def mock_render(*args, **kwargs):
        raise Exception("Template error")

    # Apply the patch
    monkeypatch.setattr("basic_memory.api.template_loader.TemplateLoader.render", mock_render)

    # Test continue_conversation error handling
    response = await client.post(
        f"{project_url}/prompt/continue-conversation",
        json={"topic": "test error", "timeframe": "7d"},
    )

    assert response.status_code == 500
    assert "detail" in response.json()
    assert "Template error" in response.json()["detail"]

    # Test search_prompt error handling
    response = await client.post(
        f"{project_url}/prompt/search", json={"query": "test error", "timeframe": "7d"}
    )

    assert response.status_code == 500
    assert "detail" in response.json()
    assert "Template error" in response.json()["detail"]
