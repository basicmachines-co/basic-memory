"""Telemetry coverage for async client auth failures."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

async_client_module = importlib.import_module("basic_memory.mcp.async_client")


@pytest.mark.asyncio
async def test_resolve_cloud_token_emits_failure_span(monkeypatch) -> None:
    spans: list[tuple[str, dict]] = []
    error_messages: list[str] = []

    class FakeAuth:
        def __init__(self, client_id: str, authkit_domain: str) -> None:
            self.client_id = client_id
            self.authkit_domain = authkit_domain

        async def get_valid_token(self):
            return None

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    monkeypatch.setattr(async_client_module.telemetry, "span", fake_span)
    monkeypatch.setattr("basic_memory.cli.auth.CLIAuth", FakeAuth)
    monkeypatch.setattr(async_client_module.logger, "error", error_messages.append)

    config = SimpleNamespace(
        cloud_api_key=None,
        cloud_client_id="client-123",
        cloud_domain="auth.example.com",
    )

    with pytest.raises(RuntimeError, match="no credentials found"):
        await async_client_module._resolve_cloud_token(config)

    assert spans == [("routing.resolve_cloud_credentials", {"has_api_key": False})]
    assert error_messages == ["Cloud routing requested but no credentials were available"]
