import json
import os
import stat
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from basic_memory.cli.auth import CLIAuth


def _make_mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_cli_auth_request_device_authorization_uses_injected_http_client(tmp_path, monkeypatch):
    """Integration-style test: exercise the request flow with real httpx plumbing (MockTransport)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_ENV", "test")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth2/device_authorization")
        body = (await request.aread()).decode()
        # sanity: client_id should be in form data
        assert "client_id=test-client-id" in body
        return httpx.Response(
            200,
            json={
                "device_code": "devcode",
                "user_code": "usercode",
                "verification_uri": "https://example.test/verify",
                "interval": 1,
            },
        )

    transport = _make_mock_transport(handler)

    @asynccontextmanager
    async def client_factory():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    auth = CLIAuth(
        client_id="test-client-id",
        authkit_domain="https://example.test",
        http_client_factory=client_factory,
    )

    result = await auth.request_device_authorization()
    assert result is not None
    assert result["device_code"] == "devcode"


def test_cli_auth_generate_pkce_pair_format(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_ENV", "test")

    auth = CLIAuth(client_id="cid", authkit_domain="https://example.test")
    verifier, challenge = auth.generate_pkce_pair()

    # PKCE verifier/challenge should be URL-safe base64 without padding.
    assert verifier
    assert challenge
    assert "=" not in verifier
    assert "=" not in challenge
    # code verifier length should be in recommended bounds (rough sanity).
    assert 43 <= len(verifier) <= 128


@pytest.mark.asyncio
async def test_cli_auth_save_load_and_get_valid_token_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_ENV", "test")

    auth = CLIAuth(client_id="cid", authkit_domain="https://example.test")

    tokens = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "token_type": "Bearer"}
    auth.save_tokens(tokens)

    loaded = auth.load_tokens()
    assert loaded is not None
    assert loaded["access_token"] == "at"
    assert loaded["refresh_token"] == "rt"
    assert auth.is_token_valid(loaded) is True

    valid = await auth.get_valid_token()
    assert valid == "at"

    # Permission should be 600 on POSIX systems
    if os.name != "nt":
        mode = auth.token_file.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


@pytest.mark.asyncio
async def test_cli_auth_refresh_flow_uses_injected_http_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_ENV", "test")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            body = (await request.aread()).decode()
            assert "grant_type=refresh_token" in body
            return httpx.Response(
                200,
                json={
                    "access_token": "new-at",
                    "refresh_token": "new-rt",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = _make_mock_transport(handler)

    @asynccontextmanager
    async def client_factory():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    auth = CLIAuth(
        client_id="cid",
        authkit_domain="https://example.test",
        http_client_factory=client_factory,
    )

    # Write an expired token file manually (so we control expires_at precisely).
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        json.dumps(
            {
                "access_token": "old-at",
                "refresh_token": "old-rt",
                "expires_at": int(time.time()) - 10,
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )

    token = await auth.get_valid_token()
    assert token == "new-at"


