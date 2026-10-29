"""Telemetry for the published bm_* tools: API request spans and the routed project.

The spans come from the real stack, captured with logfire's test exporter.
"""

from collections import Counter
from typing import Any

import pytest
from logfire.testing import CaptureLogfire, capfire as capfire

from basic_memory.mcp.tools import bm_context, bm_edit, bm_read, bm_search, bm_write


def _spans(capfire: CaptureLogfire) -> list[tuple[str, dict[str, Any]]]:
    return [
        (span["name"], span["attributes"])
        for span in capfire.exporter.exported_spans_as_dict()
        if span["attributes"].get("logfire.span_type") != "pending_span"
    ]


def _api_requests(spans: list[tuple[str, dict[str, Any]]]) -> Counter[str]:
    """How many times each typed client called the API."""
    return Counter(name for name, _ in spans if name.startswith("api.request."))


def _routed_to(spans: list[tuple[str, dict[str, Any]]], project_name: str) -> bool:
    return any(
        name == "routing.client_session"
        and attrs.get("project_name") == project_name
        and attrs.get("route_mode") == "local_asgi"
        for name, attrs in spans
    )


@pytest.mark.asyncio
async def test_bm_read_resolves_once_and_reads_the_entity_once(
    app, test_project, capfire: CaptureLogfire
) -> None:
    await bm_write(
        project=test_project.name,
        title="Readable Telemetry Note",
        directory="notes",
        content="Readable telemetry content",
    )
    capfire.exporter.clear()

    await bm_read("notes/readable-telemetry-note", project=test_project.name, output_format="json")

    spans = _spans(capfire)
    # Accepted content comes with the entity, so the resource route is never read.
    assert _api_requests(spans) == Counter(
        {"api.request.knowledge.resolve_entity": 1, "api.request.knowledge.get_entity": 1}
    )
    assert _routed_to(spans, test_project.name)


@pytest.mark.asyncio
async def test_bm_edit_resolves_then_patches(app, test_project, capfire: CaptureLogfire) -> None:
    await bm_write(
        project=test_project.name,
        title="Editable Telemetry Note",
        directory="notes",
        content="Original telemetry content",
    )
    capfire.exporter.clear()

    await bm_edit(
        "notes/editable-telemetry-note",
        command="append",
        content="\n\nAppended telemetry content",
        project=test_project.name,
        output_format="json",
    )

    spans = _spans(capfire)
    assert _api_requests(spans) == Counter(
        {"api.request.knowledge.resolve_entity": 1, "api.request.knowledge.edit_entity": 1}
    )
    assert _routed_to(spans, test_project.name)


@pytest.mark.asyncio
async def test_bm_search_records_the_search_request(
    app, test_project, capfire: CaptureLogfire
) -> None:
    await bm_write(
        project=test_project.name,
        title="Searchable Telemetry Note",
        directory="notes",
        content="Telemetry search content",
        tags=["telemetry"],
    )
    capfire.exporter.clear()

    await bm_search(query="telemetry", mode="text", project=test_project.name, output_format="json")

    spans = _spans(capfire)
    assert _api_requests(spans) == Counter({"api.request.search": 1})
    assert _routed_to(spans, test_project.name)


@pytest.mark.asyncio
async def test_bm_context_records_the_context_request(
    app, test_project, capfire: CaptureLogfire
) -> None:
    await bm_write(
        project=test_project.name,
        title="Context Telemetry Note",
        directory="notes",
        content="Context telemetry content",
    )
    capfire.exporter.clear()

    await bm_context(
        identifier="memory://notes/context-telemetry-note",
        project=test_project.name,
        depth=2,
        since="7d",
        page=1,
        page_size=5,
        max_related=3,
        output_format="json",
    )

    spans = _spans(capfire)
    assert _api_requests(spans) == Counter({"api.request.memory.build_context": 1})
    assert any(
        name == "mcp.client.memory.build_context"
        and {
            "client_name": "memory",
            "operation": "build_context",
            "page": 1,
            "page_size": 5,
        }.items()
        <= attrs.items()
        for name, attrs in spans
    )
    assert _routed_to(spans, test_project.name)
