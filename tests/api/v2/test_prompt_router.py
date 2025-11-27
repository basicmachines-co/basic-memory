"""Tests for V2 prompt router endpoints (ID-based)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from basic_memory.models import Project
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
    v2_project_url: str,
):
    """Test the v2 continue_conversation endpoint with real services."""
    # Create request data
    request_data = {
        "topic": "Root",  # This should match our test entity in test_graph
        "timeframe": "7d",
        "depth": 1,
        "related_items_limit": 2,
    }

    # Call the endpoint
    response = await client.post(
        f"{v2_project_url}/prompt/continue-conversation", json=request_data
    )

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


@pytest.mark.asyncio
async def test_continue_conversation_without_topic(
    client: AsyncClient,
    entity_service,
    search_service,
    context_service,
    entity_repository,
    test_graph,
    v2_project_url: str,
):
    """Test v2 continue_conversation without topic - should use recent activity."""
    request_data = {"timeframe": "1d", "depth": 1, "related_items_limit": 2}

    response = await client.post(
        f"{v2_project_url}/prompt/continue-conversation", json=request_data
    )

    assert response.status_code == 200
    result = response.json()
    assert "Recent Activity" in result["context"]["topic"]


@pytest.mark.asyncio
async def test_search_prompt_endpoint(
    client: AsyncClient, entity_service, search_service, test_graph, v2_project_url: str
):
    """Test the v2 search_prompt endpoint with real services."""
    # Create request data
    request_data = {
        "query": "Root",  # This should match our test entity
        "timeframe": "7d",
    }

    # Call the endpoint
    response = await client.post(f"{v2_project_url}/prompt/search", json=request_data)

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
    client: AsyncClient, entity_service, search_service, v2_project_url: str
):
    """Test the v2 search_prompt endpoint with a query that returns no results."""
    # Create request data with a query that shouldn't match anything
    request_data = {"query": "NonExistentQuery12345", "timeframe": "7d"}

    # Call the endpoint
    response = await client.post(f"{v2_project_url}/prompt/search", json=request_data)

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
async def test_error_handling(client: AsyncClient, monkeypatch, v2_project_url: str):
    """Test error handling in v2 endpoints by breaking the template loader."""

    # Patch the template loader to raise an exception
    def mock_render(*args, **kwargs):
        raise Exception("Template error")

    # Apply the patch
    monkeypatch.setattr("basic_memory.api.template_loader.TemplateLoader.render", mock_render)

    # Test continue_conversation error handling
    response = await client.post(
        f"{v2_project_url}/prompt/continue-conversation",
        json={"topic": "test error", "timeframe": "7d"},
    )

    assert response.status_code == 500
    assert "detail" in response.json()
    assert "Template error" in response.json()["detail"]

    # Test search_prompt error handling
    response = await client.post(
        f"{v2_project_url}/prompt/search", json={"query": "test error", "timeframe": "7d"}
    )

    assert response.status_code == 500
    assert "detail" in response.json()
    assert "Template error" in response.json()["detail"]


@pytest.mark.asyncio
async def test_v2_prompt_endpoints_use_project_id_not_name(
    client: AsyncClient, test_project: Project
):
    """Verify v2 prompt endpoints require project ID, not name."""
    # Try using project name instead of ID - should fail
    response = await client.post(
        f"/v2/projects/{test_project.name}/prompt/continue-conversation",
        json={"topic": "test", "timeframe": "7d"},
    )

    # Should get validation error or 404 because name is not a valid integer
    assert response.status_code in [404, 422]

    # Also test search endpoint
    response = await client.post(
        f"/v2/projects/{test_project.name}/prompt/search",
        json={"query": "test", "timeframe": "7d"},
    )

    assert response.status_code in [404, 422]


@pytest.mark.asyncio
async def test_prompt_invalid_project_id(client: AsyncClient):
    """Test prompt endpoints with invalid project ID return 404."""
    # Test continue-conversation
    response = await client.post(
        "/v2/projects/999999/prompt/continue-conversation",
        json={"topic": "test", "timeframe": "7d"},
    )
    assert response.status_code == 404

    # Test search
    response = await client.post(
        "/v2/projects/999999/prompt/search",
        json={"query": "test", "timeframe": "7d"},
    )
    assert response.status_code == 404
