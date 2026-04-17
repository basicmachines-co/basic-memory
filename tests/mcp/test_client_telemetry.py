"""Telemetry coverage for typed MCP clients and shared HTTP helpers."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import httpx
import logfire
import pytest
from mcp.server.fastmcp.exceptions import ToolError

knowledge_client_module = importlib.import_module("basic_memory.mcp.clients.knowledge")
search_client_module = importlib.import_module("basic_memory.mcp.clients.search")
utils_module = importlib.import_module("basic_memory.mcp.tools.utils")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))

        class FakeSpan:
            def set_attribute(self, key: str, value) -> None:
                attrs[key] = value

            def set_attributes(self, new_attrs: dict) -> None:
                attrs.update(new_attrs)

        yield FakeSpan()

    return spans, fake_span


@pytest.mark.asyncio
async def test_knowledge_client_resolve_entity_emits_client_and_http_spans(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json={"external_id": "entity-123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        knowledge_client = knowledge_client_module.KnowledgeClient(client, "project-123")
        resolved = await knowledge_client.resolve_entity("notes/root", strict=True)

    assert resolved == "entity-123"
    assert [name for name, _ in spans] == [
        "mcp.client.knowledge.resolve_entity",
        "mcp.http.request",
    ]
    assert spans[1][1] == {
        "method": "POST",
        "client_name": "knowledge",
        "operation": "resolve_entity",
        "path_template": "/v2/projects/{project_id}/knowledge/resolve",
        "phase": "request",
        "has_query": False,
        "has_body": True,
        "status_code": 200,
        "is_success": True,
        "outcome": "success",
    }


@pytest.mark.asyncio
async def test_search_client_emits_client_and_http_spans(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "results": [],
                "current_page": 2,
                "page_size": 5,
                "has_more": False,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        search_client = search_client_module.SearchClient(client, "project-123")
        response = await search_client.search({"text": "telemetry"}, page=2, page_size=5)

    assert response.current_page == 2
    assert [name for name, _ in spans] == [
        "mcp.client.search.search",
        "mcp.http.request",
    ]
    assert spans[1][1] == {
        "method": "POST",
        "client_name": "search",
        "operation": "search",
        "path_template": "/v2/projects/{project_id}/search/",
        "phase": "request",
        "has_query": True,
        "has_body": True,
        "status_code": 200,
        "is_success": True,
        "outcome": "success",
    }


@pytest.mark.asyncio
async def test_call_get_emits_http_outcome_for_client_errors(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(404, json={"detail": "missing"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        with pytest.raises(ToolError, match="missing"):
            await utils_module.call_get(
                client,
                "/missing",
                client_name="knowledge",
                operation="get_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}",
            )

    assert spans == [
        (
            "mcp.http.request",
            {
                "method": "GET",
                "client_name": "knowledge",
                "operation": "get_entity",
                "path_template": "/v2/projects/{project_id}/knowledge/entities/{entity_id}",
                "phase": "request",
                "has_query": False,
                "has_body": False,
                "status_code": 404,
                "is_success": False,
                "outcome": "client_error",
            },
        )
    ]


@pytest.mark.asyncio
async def test_call_get_emits_transport_error_outcome(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        with pytest.raises(httpx.ConnectError, match="boom"):
            await utils_module.call_get(
                client,
                "/boom",
                client_name="knowledge",
                operation="get_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}",
            )

    assert spans == [
        (
            "mcp.http.request",
            {
                "method": "GET",
                "client_name": "knowledge",
                "operation": "get_entity",
                "path_template": "/v2/projects/{project_id}/knowledge/entities/{entity_id}",
                "phase": "request",
                "has_query": False,
                "has_body": False,
                "is_success": False,
                "outcome": "transport_error",
                "error_type": "ConnectError",
            },
        )
    ]
