"""Debug-level step spans cost nothing when Logfire exports at info.

Basic Memory Cloud production configures Logfire with ``min_level="info"``. The
routing and typed-client step spans are created at debug, so there they must
export no record and their children must attach to the enclosing span. At debug
the full tree comes back.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Literal

import httpx
import logfire
import pytest
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from basic_memory.telemetry import debug_span

knowledge_client_module = importlib.import_module("basic_memory.mcp.clients.knowledge")
project_context = importlib.import_module("basic_memory.mcp.project_context")

type MinLevel = Literal["info", "debug"]
type ConfigureAt = Callable[[MinLevel], TestExporter]


@pytest.fixture
def configure_at() -> ConfigureAt:
    """Configure Logfire at a min_level and capture what it exports."""

    def configure(min_level: MinLevel) -> TestExporter:
        exporter = TestExporter()
        logfire.configure(
            send_to_logfire=False,
            console=False,
            min_level=min_level,
            additional_span_processors=[SimpleSpanProcessor(exporter)],
        )
        return exporter

    return configure


def _finished(exporter: TestExporter) -> list[dict[str, Any]]:
    return [
        span
        for span in exporter.exported_spans_as_dict()
        if span["attributes"].get("logfire.span_type") != "pending_span"
    ]


def _parent_names(exporter: TestExporter) -> dict[str, str | None]:
    """Each exported span's name mapped to its parent's name."""
    spans = _finished(exporter)
    names = {span["context"]["span_id"]: span["name"] for span in spans}
    return {
        span["name"]: names.get(span["parent"]["span_id"]) if span["parent"] else None
        for span in spans
    }


def test_debug_span_exports_nothing_at_info_and_its_child_moves_up(
    configure_at: ConfigureAt,
) -> None:
    exporter = configure_at("info")

    with logfire.span("request"):
        with debug_span("step", detail="dropped") as step:
            step.set_attribute("also", "dropped")
            with logfire.span("work"):
                pass

    assert _parent_names(exporter) == {"request": None, "work": "request"}


def test_debug_span_is_exported_at_debug(configure_at: ConfigureAt) -> None:
    exporter = configure_at("debug")

    with logfire.span("request"):
        with debug_span("step", detail="kept"):
            with logfire.span("work"):
                pass

    assert _parent_names(exporter) == {"request": None, "step": "request", "work": "step"}
    [step] = [span for span in _finished(exporter) if span["name"] == "step"]
    assert step["attributes"]["detail"] == "kept"


async def _resolve_entity_through_typed_client() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"external_id": "entity-123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.test") as client:
        knowledge_client = knowledge_client_module.KnowledgeClient(client, "project-123")
        with logfire.span("tools/call"):
            await knowledge_client.resolve_entity("notes/root", strict=True)


@pytest.mark.asyncio
async def test_typed_client_span_is_dropped_at_info_and_http_request_is_kept(
    configure_at: ConfigureAt,
) -> None:
    exporter = configure_at("info")

    await _resolve_entity_through_typed_client()

    # mcp.http.request stays at info: it is the only record of the API call in
    # a hosted MCP trace, and it carries the operation and HTTP outcome.
    assert _parent_names(exporter) == {"tools/call": None, "mcp.http.request": "tools/call"}
    [request_span] = [span for span in _finished(exporter) if span["name"] == "mcp.http.request"]
    assert request_span["attributes"]["operation"] == "resolve_entity"
    assert request_span["attributes"]["outcome"] == "success"


@pytest.mark.asyncio
async def test_typed_client_span_is_exported_at_debug(configure_at: ConfigureAt) -> None:
    exporter = configure_at("debug")

    await _resolve_entity_through_typed_client()

    assert _parent_names(exporter) == {
        "tools/call": None,
        "mcp.client.knowledge.resolve_entity": "tools/call",
        "mcp.http.request": "mcp.client.knowledge.resolve_entity",
    }


async def _open_project_client(project_name: str) -> None:
    with logfire.span("tools/call"):
        async with project_context.get_project_client(project=project_name):
            pass


ROUTING_SPANS = {"routing.client_session", "routing.validate_project", "routing.resolve_project"}


@pytest.mark.asyncio
async def test_routing_spans_are_dropped_at_info(app, test_project, configure_at) -> None:
    exporter = configure_at("info")

    await _open_project_client(test_project.name)

    parents = _parent_names(exporter)
    assert not ROUTING_SPANS & parents.keys()
    # The validation request that ran inside the routing spans attaches to the tool span.
    assert parents["mcp.http.request"] == "tools/call"


@pytest.mark.asyncio
async def test_routing_spans_are_exported_at_debug(app, test_project, configure_at) -> None:
    exporter = configure_at("debug")

    await _open_project_client(test_project.name)

    parents = _parent_names(exporter)
    assert ROUTING_SPANS <= parents.keys()
    assert parents["routing.client_session"] == "tools/call"
    assert parents["routing.validate_project"] == "routing.client_session"
    assert parents["mcp.http.request"] == "routing.validate_project"
