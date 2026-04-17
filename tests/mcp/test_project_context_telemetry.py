"""Telemetry coverage for project routing helpers."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Any, cast

import logfire
import pytest

from basic_memory.config import ProjectEntry
from basic_memory.schemas.cloud import WorkspaceInfo

project_context = importlib.import_module("basic_memory.mcp.project_context")


class _ContextState:
    """Minimal FastMCP context-state stub for unit tests."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    async def get_state(self, key: str):
        return self._state.get(key)

    async def set_state(self, key: str, value: object, **kwargs) -> None:
        self._state[key] = value


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


@pytest.mark.asyncio
async def test_resolve_workspace_parameter_emits_routing_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    context = _ContextState()
    workspace = WorkspaceInfo(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_type="personal",
        name="Personal",
        role="owner",
    )

    async def fake_get_available_workspaces(context=None):
        return [workspace]

    monkeypatch.setattr(logfire, "span", fake_span)
    monkeypatch.setattr(project_context, "get_available_workspaces", fake_get_available_workspaces)

    resolved = await project_context.resolve_workspace_parameter(context=cast(Any, context))

    assert resolved.tenant_id == workspace.tenant_id
    assert spans == [
        (
            "routing.resolve_workspace",
            {"workspace_requested": False, "has_context": True},
        )
    ]


@pytest.mark.asyncio
async def test_get_project_client_contextualizes_route_mode(config_manager, monkeypatch) -> None:
    spans, fake_span = _capture_spans()

    config = config_manager.load_config()
    (config_manager.config_dir.parent / "main").mkdir(parents=True, exist_ok=True)
    config.projects["main"] = ProjectEntry(path=str(config_manager.config_dir.parent / "main"))
    config_manager.save_config(config)

    monkeypatch.setattr(logfire, "span", fake_span)

    with pytest.raises(Exception):
        async with project_context.get_project_client(project="main"):
            pass

    span_names = [name for name, _ in spans]
    assert "routing.resolve_project" in span_names
    assert "routing.client_session" in span_names
    assert "routing.validate_project" in span_names
    # route_mode + project_name are now carried as span attrs on routing.client_session
    client_session_attrs = [attrs for name, attrs in spans if name == "routing.client_session"]
    assert any(
        attrs.get("project_name") == "main" and attrs.get("route_mode") == "local_asgi"
        for attrs in client_session_attrs
    )
