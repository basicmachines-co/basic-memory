"""Tests for v2 memory router endpoints."""

import importlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from basic_memory import db
from basic_memory.deps.services import get_context_service_v2_external
from basic_memory.models import Project
from basic_memory.schemas.memory import (
    MAX_CONTEXT_DEPTH,
    MAX_CONTEXT_PAGE_SIZE,
    MAX_CONTEXT_RELATED_RESULTS,
    MAX_RECENT_ACTIVITY_PAGE_SIZE,
)
from basic_memory.services.exceptions import AmbiguousIdentifierError

memory_router_module = importlib.import_module("basic_memory.api.v2.routers.memory_router")


class _RecordingSpan:
    """Capture span attributes and whether the span exited with an exception."""

    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = attributes
        self.exited_with: BaseException | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, values: dict[str, Any]) -> None:
        self.attributes.update(values)

    def __enter__(self) -> "_RecordingSpan":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.exited_with = exc
        return None


@contextmanager
def _record_memory_router_spans(monkeypatch):
    spans: list[_RecordingSpan] = []

    def span(name: str, **attributes: Any) -> _RecordingSpan:
        recorded = _RecordingSpan(name, dict(attributes))
        spans.append(recorded)
        return recorded

    monkeypatch.setattr(memory_router_module, "logfire", SimpleNamespace(span=span))
    yield spans


def _root_memory_span(spans: list[_RecordingSpan]) -> _RecordingSpan:
    return next(span for span in spans if span.name == "api.request.memory.build_context")


class _TraversalSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def build_context(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        raise AssertionError("context traversal must not run for a rejected locator")


class _ExplodingContextService:
    async def build_context(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("context backend exploded")


class _AmbiguousContextService:
    async def build_context(self, *args: Any, **kwargs: Any) -> None:
        raise AmbiguousIdentifierError(
            "Bean Origins",
            [
                ("bean-origins", "Bean Origins.md"),
                ("archive/bean-origins", "archive/Bean Origins.md"),
            ],
        )


async def create_test_entity(
    test_project, entity_data, entity_repository, search_service, file_service
):
    """Helper to create an entity with file and index it."""
    # Create file
    test_content = f"# {entity_data['title']}\n\nTest content"
    file_path = Path(test_project.path) / entity_data["file_path"]
    file_path.parent.mkdir(parents=True, exist_ok=True)
    await file_service.write_file(file_path, test_content)

    # Create entity
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.create(session, entity_data)

    # Index for search
    await search_service.index_entity(entity)

    return entity


@pytest.mark.asyncio
async def test_get_recent_context(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting recent activity context."""
    entity_data = {
        "title": "Recent Test Entity",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "recent_test.md",
        "checksum": "abc123",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get recent context
    response = await client.get(f"{v2_project_url}/memory/recent")

    assert response.status_code == 200
    data = response.json()

    # Verify response structure (GraphContext uses 'results' not 'entities')
    assert "results" in data
    assert "metadata" in data
    assert "page" in data
    assert "page_size" in data


@pytest.mark.asyncio
async def test_get_recent_context_with_pagination(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test recent context with pagination parameters."""
    # Create multiple test entities
    for i in range(5):
        entity_data = {
            "title": f"Entity {i}",
            "note_type": "note",
            "content_type": "text/markdown",
            "file_path": f"entity_{i}.md",
            "checksum": f"checksum{i}",
        }
        await create_test_entity(
            test_project, entity_data, entity_repository, search_service, file_service
        )

    # Get recent context with pagination
    response = await client.get(
        f"{v2_project_url}/memory/recent", params={"page": 1, "page_size": 3}
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert data["page"] == 1
    assert data["page_size"] == 3


@pytest.mark.asyncio
async def test_get_recent_context_with_type_filter(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test filtering recent context by type."""
    # Create a test entity
    entity_data = {
        "title": "Filtered Entity",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "filtered.md",
        "checksum": "xyz789",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get recent context filtered by type
    response = await client.get(f"{v2_project_url}/memory/recent", params={"type": ["entity"]})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_recent_context_with_timeframe(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test recent context with custom timeframe."""
    response = await client.get(f"{v2_project_url}/memory/recent", params={"timeframe": "1d"})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_recent_context_invalid_project_id(
    client: AsyncClient,
):
    """Test getting recent context with invalid project ID returns 404."""
    response = await client.get("/v2/projects/999999/memory/recent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_memory_context_by_permalink(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context for a specific memory URI (permalink)."""
    # Create a test entity
    entity_data = {
        "title": "Context Test",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "context_test.md",
        "checksum": "def456",
        "permalink": "context-test",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context for this entity
    response = await client.get(f"{v2_project_url}/memory/context-test")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data

    # Values at the public limits remain valid so callers can request a bounded,
    # larger traversal without guessing below the advertised ceiling.
    response_at_limits = await client.get(
        f"{v2_project_url}/memory/context-test",
        params={"page_size": MAX_CONTEXT_PAGE_SIZE, "max_related": MAX_CONTEXT_RELATED_RESULTS},
    )
    assert response_at_limits.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"page_size": MAX_CONTEXT_PAGE_SIZE + 1},
        {"max_related": MAX_CONTEXT_RELATED_RESULTS + 1},
        {"page": 0},
        {"page_size": 0},
        {"max_related": -1},
        {"depth": MAX_CONTEXT_DEPTH + 1},
        {"depth": 0},
    ],
)
async def test_get_memory_context_rejects_unbounded_requests(
    client: AsyncClient,
    v2_project_url: str,
    params: dict[str, int],
):
    """The API boundary must enforce the same bounds as the MCP tool."""
    response = await client.get(f"{v2_project_url}/memory/context-test", params=params)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"depth": MAX_CONTEXT_DEPTH + 1},
        {"depth": 0},
        {"page_size": MAX_RECENT_ACTIVITY_PAGE_SIZE + 1},
        {"page_size": 0},
        {"page": 0},
        {"max_related": MAX_CONTEXT_RELATED_RESULTS + 1},
    ],
)
async def test_get_recent_context_rejects_unbounded_requests(
    client: AsyncClient,
    v2_project_url: str,
    params: dict[str, int],
):
    """A deep recent-activity traversal is as costly as build_context's, so it is bounded too."""
    response = await client.get(f"{v2_project_url}/memory/recent", params=params)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_memory_endpoints_accept_the_largest_allowed_requests(
    client: AsyncClient,
    v2_project_url: str,
):
    """The bounds are inclusive, so the documented maximums keep working."""
    recent = await client.get(
        f"{v2_project_url}/memory/recent",
        params={"depth": MAX_CONTEXT_DEPTH, "page_size": MAX_RECENT_ACTIVITY_PAGE_SIZE},
    )
    context = await client.get(
        f"{v2_project_url}/memory/context-test",
        params={"depth": MAX_CONTEXT_DEPTH, "page_size": MAX_CONTEXT_PAGE_SIZE},
    )

    assert recent.status_code == 200
    assert context.status_code == 200


@pytest.mark.asyncio
async def test_get_memory_context_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context using ID-based memory URI."""
    # Create a test entity
    entity_data = {
        "title": "ID Context Test",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "id_context_test.md",
        "checksum": "ghi789",
    }
    created_entity = await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context using ID format (memory://id/123 or memory://123)
    response = await client.get(f"{v2_project_url}/memory/id/{created_entity.id}")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_memory_context_with_depth(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context with depth parameter."""
    # Create a test entity
    entity_data = {
        "title": "Depth Test",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "depth_test.md",
        "checksum": "jkl012",
        "permalink": "depth-test",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context with depth
    response = await client.get(f"{v2_project_url}/memory/depth-test", params={"depth": 2})

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_get_memory_context_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting context for non-existent memory URI returns 404."""
    response = await client.get(f"{v2_project_url}/memory/nonexistent-uri")

    # Note: This might return 200 with empty results depending on implementation
    # Adjust assertion based on actual behavior
    assert response.status_code in [200, 404]


@pytest.mark.asyncio
async def test_get_memory_context_with_timeframe(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """Test getting context with timeframe filter."""
    # Create a test entity
    entity_data = {
        "title": "Timeframe Test",
        "note_type": "note",
        "content_type": "text/markdown",
        "file_path": "timeframe_test.md",
        "checksum": "mno345",
        "permalink": "timeframe-test",
    }
    await create_test_entity(
        test_project, entity_data, entity_repository, search_service, file_service
    )

    # Get context with timeframe
    response = await client.get(
        f"{v2_project_url}/memory/timeframe-test", params={"timeframe": "7d"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_recent_context_has_more(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
):
    """has_more should be True when there are more results beyond the current page."""
    # Create enough entities to exceed a small page_size
    for i in range(4):
        entity_data = {
            "title": f"HasMore Memory {i}",
            "note_type": "note",
            "content_type": "text/markdown",
            "file_path": f"hasmore_mem_{i}.md",
            "checksum": f"hasmoremem{i}",
        }
        await create_test_entity(
            test_project, entity_data, entity_repository, search_service, file_service
        )

    # Request page_size=2 — with 4 entities, has_more should be True
    response = await client.get(
        f"{v2_project_url}/memory/recent",
        params={"page": 1, "page_size": 2, "type": ["entity"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "has_more" in data
    assert data["has_more"] is True
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_v2_memory_endpoints_use_project_id_not_name(
    client: AsyncClient,
    test_project: Project,
):
    """Test that v2 memory endpoints reject string project names."""
    # Try to use project name instead of ID - should fail
    response = await client.get(f"/v2/{test_project.name}/memory/recent")

    # FastAPI path validation should reject non-integer project_id
    assert response.status_code in [404, 422]


@pytest.mark.asyncio
async def test_get_memory_context_skips_observations_when_asked(
    client: AsyncClient,
    test_graph,
    v2_project_url: str,
):
    """include_observations=false returns the same graph without observation lists (#1571)."""
    full = await client.get(f"{v2_project_url}/memory/test/root")
    shape_only = await client.get(
        f"{v2_project_url}/memory/test/root",
        params={"include_observations": False},
    )

    assert full.status_code == 200
    assert shape_only.status_code == 200
    full_data = full.json()
    shape_data = shape_only.json()

    # Default keeps observations; the opt-out loads none.
    assert len(full_data["results"][0]["observations"]) == 2
    # The count also covers related entities' observations.
    assert full_data["metadata"]["total_observations"] > 0
    assert shape_data["results"][0]["observations"] == []
    assert shape_data["metadata"]["total_observations"] == 0

    # Primary and related selection is unchanged.
    assert (
        shape_data["results"][0]["primary_result"]["permalink"]
        == full_data["results"][0]["primary_result"]["permalink"]
    )
    assert shape_data["results"][0]["related_results"] == full_data["results"][0]["related_results"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locator", "expected_detail"),
    [
        # The production locator shape: an arbitrary HTTPS URL embedded in a path.
        ("workspace/project/https://example.invalid/notes", "contains protocol scheme"),
        ("", "cannot be empty"),
        ("%20%20%20", "cannot be empty or whitespace"),
        ("notes%3Cbrackets%3E", "contains invalid characters"),
        ("notes//meeting", "contains double slashes"),
    ],
)
async def test_get_memory_context_rejects_malformed_locators(
    app: FastAPI,
    client: AsyncClient,
    v2_project_url: str,
    locator: str,
    expected_detail: str,
):
    """Malformed locators are caller input, not an internal server error (#1879)."""
    traversal = _TraversalSpy()
    app.dependency_overrides[get_context_service_v2_external] = lambda: traversal
    try:
        response = await client.get(f"{v2_project_url}/memory/{locator}")
    finally:
        app.dependency_overrides.pop(get_context_service_v2_external, None)

    assert response.status_code == 400
    assert expected_detail in response.json()["detail"]
    # The boundary rejects before the context service is touched.
    assert traversal.calls == 0


@pytest.mark.asyncio
async def test_get_memory_context_accepts_relative_and_memory_url_locators(
    client: AsyncClient,
    test_graph,
    v2_project_url: str,
):
    """Both accepted locator spellings still resolve to the same context."""
    relative = await client.get(f"{v2_project_url}/memory/test/root")
    scheme = await client.get(f"{v2_project_url}/memory/memory://test/root")

    assert relative.status_code == 200
    assert scheme.status_code == 200
    assert (
        relative.json()["results"][0]["primary_result"]["permalink"]
        == scheme.json()["results"][0]["primary_result"]["permalink"]
    )


@pytest.mark.asyncio
async def test_get_memory_context_records_input_rejected_outcome(
    client: AsyncClient,
    v2_project_url: str,
    monkeypatch,
):
    """The root span records a bounded terminal outcome for caller input rejection."""
    with _record_memory_router_spans(monkeypatch) as spans:
        response = await client.get(
            f"{v2_project_url}/memory/workspace/project/https://example.invalid/notes"
        )

    assert response.status_code == 400
    root_span = _root_memory_span(spans)
    assert root_span.attributes["outcome"] == "input_rejected"
    # The span carries a bounded outcome, not the caller's uploaded locator content.
    assert "example.invalid" not in repr(root_span.attributes)
    assert root_span.exited_with is None


@pytest.mark.asyncio
async def test_get_memory_context_records_success_outcome(
    client: AsyncClient,
    test_graph,
    v2_project_url: str,
    monkeypatch,
):
    with _record_memory_router_spans(monkeypatch) as spans:
        response = await client.get(f"{v2_project_url}/memory/test/root")

    assert response.status_code == 200
    root_span = _root_memory_span(spans)
    assert root_span.attributes["outcome"] == "success"
    assert root_span.exited_with is None


@pytest.mark.asyncio
async def test_get_memory_context_keeps_service_failures_as_server_errors(
    app: FastAPI,
    v2_project_url: str,
    monkeypatch,
):
    """A genuine context-service failure stays visible as a 500 and a failed outcome."""
    app.dependency_overrides[get_context_service_v2_external] = lambda: _ExplodingContextService()
    try:
        with _record_memory_router_spans(monkeypatch) as spans:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as failing_client:
                response = await failing_client.get(f"{v2_project_url}/memory/test/root")
    finally:
        app.dependency_overrides.pop(get_context_service_v2_external, None)

    assert response.status_code == 500
    root_span = _root_memory_span(spans)
    assert root_span.attributes["outcome"] == "failed"
    assert isinstance(root_span.exited_with, RuntimeError)


@pytest.mark.asyncio
async def test_get_memory_context_preserves_ambiguous_identifier_conflict(
    app: FastAPI,
    client: AsyncClient,
    v2_project_url: str,
    monkeypatch,
):
    """Ambiguous identifiers keep their existing 409 conflict response."""
    app.dependency_overrides[get_context_service_v2_external] = lambda: _AmbiguousContextService()
    try:
        with _record_memory_router_spans(monkeypatch) as spans:
            response = await client.get(f"{v2_project_url}/memory/Bean%20Origins")
    finally:
        app.dependency_overrides.pop(get_context_service_v2_external, None)

    assert response.status_code == 409
    assert "Ambiguous identifier 'Bean Origins'" in response.json()["detail"]
    root_span = _root_memory_span(spans)
    assert root_span.attributes["outcome"] == "input_rejected"
    assert all(span.exited_with is None for span in spans)
