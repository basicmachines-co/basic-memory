from contextlib import asynccontextmanager
import json

import httpx
import pytest

from basic_memory.cli.auth import CLIAuth
from basic_memory.cli.commands.cloud.api_client import (
    SubscriptionRequiredError,
    make_api_request,
)
from basic_memory.cli.commands.cloud.cloud_utils import (
    CloudUtilsError,
    create_cloud_project,
    fetch_cloud_projects,
    project_exists,
)
from basic_memory.config import ProjectMode


@pytest.mark.asyncio
async def test_make_api_request_success_injects_auth_and_accept_encoding(
    config_home, config_manager
):
    # Arrange: create a token on disk so CLIAuth can authenticate without any network.
    auth = CLIAuth(client_id="cid", authkit_domain="https://auth.example.test")
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"token-123","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer token-123"
        assert request.headers.get("accept-encoding") == "identity"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def http_client_factory():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    # Act
    resp = await make_api_request(
        method="GET",
        url="https://cloud.example.test/proxy/health",
        auth=auth,
        http_client_factory=http_client_factory,
    )

    # Assert
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_make_api_request_raises_subscription_required(config_home, config_manager):
    auth = CLIAuth(client_id="cid", authkit_domain="https://auth.example.test")
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"token-123","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "detail": {
                    "error": "subscription_required",
                    "message": "Need subscription",
                    "subscribe_url": "https://example.test/subscribe",
                }
            },
        )

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def http_client_factory():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    with pytest.raises(SubscriptionRequiredError) as exc:
        await make_api_request(
            method="GET",
            url="https://cloud.example.test/proxy/health",
            auth=auth,
            http_client_factory=http_client_factory,
        )

    assert exc.value.subscribe_url == "https://example.test/subscribe"


@pytest.mark.asyncio
async def test_cloud_utils_fetch_and_exists_and_create_project(
    config_home, config_manager, monkeypatch
):
    # Point config.cloud_host at our mocked base URL
    config = config_manager.load_config()
    config.cloud_host = "https://cloud.example.test"
    config_manager.save_config(config)

    auth = CLIAuth(client_id="cid", authkit_domain="https://auth.example.test")
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"token-123","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    seen = {"create_payload": None}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/proxy/v2/projects/":
            return httpx.Response(
                200,
                json={
                    "projects": [
                        {"id": 1, "name": "alpha", "path": "alpha", "is_default": True},
                        {"id": 2, "name": "beta", "path": "beta", "is_default": False},
                    ]
                },
            )

        if request.method == "POST" and request.url.path == "/proxy/v2/projects/":
            # httpx.Request doesn't have .json(); parse bytes payload.
            seen["create_payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "message": "created",
                    "status": "success",
                    "default": False,
                    "old_project": None,
                    "new_project": {
                        "name": seen["create_payload"]["name"],
                        "path": seen["create_payload"]["path"],
                    },
                },
            )

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def http_client_factory():
        async with httpx.AsyncClient(
            transport=transport, base_url="https://cloud.example.test"
        ) as client:
            yield client

    async def api_request(**kwargs):
        return await make_api_request(auth=auth, http_client_factory=http_client_factory, **kwargs)

    projects = await fetch_cloud_projects(api_request=api_request)
    assert [p.name for p in projects.projects] == ["alpha", "beta"]

    assert await project_exists("alpha", api_request=api_request) is True
    assert await project_exists("missing", api_request=api_request) is False

    created = await create_cloud_project("My Project", api_request=api_request)
    assert created.new_project is not None
    assert created.new_project["name"] == "My Project"
    assert seen["create_payload"] is not None
    # Path should be permalink-like (kebab)
    assert seen["create_payload"]["path"] == "my-project"
    assert seen["create_payload"]["visibility"] == "workspace"


@pytest.mark.asyncio
async def test_create_cloud_project_accepts_visibility_override(config_home, config_manager):
    """Shared cloud helper should pass explicit visibility through to the API payload."""
    config = config_manager.load_config()
    config.cloud_host = "https://cloud.example.test"
    config_manager.save_config(config)

    seen_payload: dict | None = None

    async def api_request(**kwargs):
        nonlocal seen_payload
        seen_payload = kwargs["json_data"]
        return httpx.Response(
            200,
            json={
                "message": "created",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {"name": "shared-project", "path": "shared-project"},
            },
        )

    created = await create_cloud_project(
        "Shared Project",
        visibility="shared",
        api_request=api_request,
    )

    assert created.new_project is not None
    assert seen_payload == {
        "name": "Shared Project",
        "path": "shared-project",
        "set_default": False,
        "visibility": "shared",
    }


@pytest.mark.asyncio
async def test_cloud_utils_use_configured_workspace_headers(config_home, config_manager):
    """Workspace-aware cloud helpers should prefer project workspace over global default."""
    config = config_manager.load_config()
    config.cloud_host = "https://cloud.example.test"
    config.default_workspace = "default-workspace"
    config.set_project_mode("alpha", ProjectMode.CLOUD)
    config.projects["alpha"].workspace_id = "project-workspace"
    config_manager.save_config(config)

    seen: list[tuple[str, str | None]] = []

    async def api_request(**kwargs):
        seen.append(
            (
                kwargs["method"],
                (kwargs.get("headers") or {}).get("X-Workspace-ID"),
            )
        )

        if kwargs["method"] == "GET":
            return httpx.Response(
                200,
                json={
                    "projects": [{"id": 1, "name": "alpha", "path": "alpha", "is_default": True}]
                },
            )

        return httpx.Response(
            200,
            json={
                "message": "created",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {"name": "alpha", "path": "alpha"},
            },
        )

    assert await project_exists("alpha", api_request=api_request) is True
    await create_cloud_project("alpha", api_request=api_request)
    await fetch_cloud_projects(project_name="missing", api_request=api_request)

    assert seen == [
        ("GET", "project-workspace"),
        ("POST", "project-workspace"),
        ("GET", "default-workspace"),
    ]


@pytest.mark.asyncio
async def test_project_exists_surfaces_cloud_lookup_failures(config_home, config_manager):
    """project_exists should surface lookup failures instead of pretending the project is missing."""
    config = config_manager.load_config()
    config.cloud_host = "https://cloud.example.test"
    config_manager.save_config(config)

    async def api_request(**_kwargs):
        raise httpx.ConnectError("boom")

    with pytest.raises(CloudUtilsError, match="Failed to fetch cloud projects"):
        await project_exists("alpha", api_request=api_request)


@pytest.mark.asyncio
async def test_make_api_request_prefers_api_key_over_oauth(config_home, config_manager):
    """API key in config should be used without needing an OAuth token on disk."""
    # Arrange: set an API key in config, no OAuth token on disk
    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test_key_12345"
    config_manager.save_config(config)

    async def handler(request: httpx.Request) -> httpx.Response:
        # Verify the API key is sent as the Bearer token
        assert request.headers.get("authorization") == "Bearer bmc_test_key_12345"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def http_client_factory():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    # Act — no auth= parameter, no OAuth token file; should use API key from config
    resp = await make_api_request(
        method="GET",
        url="https://cloud.example.test/proxy/health",
        http_client_factory=http_client_factory,
    )

    # Assert
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_make_api_request_falls_back_to_oauth_when_no_api_key(config_home, config_manager):
    """When no API key is configured, should fall back to OAuth token."""
    # Arrange: no API key, but OAuth token on disk
    config = config_manager.load_config()
    config.cloud_api_key = None
    config_manager.save_config(config)

    auth = CLIAuth(client_id="cid", authkit_domain="https://auth.example.test")
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"oauth-token-456","refresh_token":null,'
        '"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer oauth-token-456"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def http_client_factory():
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    resp = await make_api_request(
        method="GET",
        url="https://cloud.example.test/proxy/health",
        auth=auth,
        http_client_factory=http_client_factory,
    )

    assert resp.json()["ok"] is True
