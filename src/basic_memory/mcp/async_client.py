import os
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncGenerator, AsyncIterator, Callable, Optional

from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from basic_memory.api.app import app as fastapi_app
from basic_memory.config import ConfigManager, ProjectMode


def _force_local_mode() -> bool:
    """Check if local mode is forced via environment variable."""
    return os.environ.get("BASIC_MEMORY_FORCE_LOCAL", "").lower() in ("true", "1", "yes")


def _force_cloud_mode() -> bool:
    """Check if cloud mode is forced via environment variable."""
    return os.environ.get("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes")


def _explicit_routing() -> bool:
    """Check if CLI --local/--cloud flag was explicitly passed."""
    return os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING", "").lower() in ("true", "1", "yes")


def _build_timeout() -> Timeout:
    """Create a standard timeout config used across all clients."""
    return Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=30.0,
    )


def _asgi_client(timeout: Timeout) -> AsyncClient:
    """Create a local ASGI client."""
    return AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
    )


async def _resolve_cloud_token(config) -> str:
    """Resolve cloud token with API key preferred, OAuth fallback."""
    token = config.cloud_api_key
    if token:
        return token

    from basic_memory.cli.auth import CLIAuth

    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    token = await auth.get_valid_token()
    if token:
        return token

    raise RuntimeError(
        "Cloud routing requested but no credentials found. "
        "Run 'bm cloud set-key <key>' or 'bm cloud login' first."
    )


async def _cloud_client(
    config,
    timeout: Timeout,
    workspace: Optional[str] = None,
) -> AsyncGenerator[AsyncClient, None]:
    """Create a cloud proxy client with resolved credentials."""
    token = await _resolve_cloud_token(config)
    proxy_base_url = f"{config.cloud_host}/proxy"
    headers = {"Authorization": f"Bearer {token}"}
    if workspace:
        headers["X-Workspace-ID"] = workspace
    logger.info(f"Creating HTTP client for cloud proxy at: {proxy_base_url}")
    async with AsyncClient(
        base_url=proxy_base_url,
        headers=headers,
        timeout=timeout,
    ) as client:
        yield client


@asynccontextmanager
async def get_cloud_control_plane_client() -> AsyncIterator[AsyncClient]:
    """Create a control-plane cloud client for endpoints outside /proxy."""
    config = ConfigManager().config
    timeout = _build_timeout()
    token = await _resolve_cloud_token(config)
    logger.info(f"Creating HTTP client for cloud control plane at: {config.cloud_host}")
    async with AsyncClient(
        base_url=config.cloud_host,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    ) as client:
        yield client


# Optional factory override for dependency injection
_client_factory: Optional[Callable[[], AbstractAsyncContextManager[AsyncClient]]] = None


def set_client_factory(factory: Callable[[], AbstractAsyncContextManager[AsyncClient]]) -> None:
    """Override the default client factory (for cloud app, testing, etc)."""
    global _client_factory
    _client_factory = factory


@asynccontextmanager
async def get_client(
    project_name: Optional[str] = None,
    workspace: Optional[str] = None,
) -> AsyncIterator[AsyncClient]:
    """Get an AsyncClient as a context manager.

    Routing priority:
    1. Factory injection.
    2. Explicit routing flags (--local/--cloud).
    3. Per-project mode routing when project_name is provided.
    4. Local ASGI transport by default.
    """
    if _client_factory:
        async with _client_factory() as client:
            yield client
        return

    config = ConfigManager().config
    timeout = _build_timeout()

    # --- Explicit routing override ---
    # Trigger: user passed --local/--cloud.
    # Why: command-level override should be deterministic and bypass project mode.
    # Outcome: route strictly based on explicit flag.
    if _explicit_routing():
        if _force_local_mode():
            logger.info("Explicit local routing enabled - using ASGI client")
            async with _asgi_client(timeout) as client:
                yield client
            return

        if _force_cloud_mode():
            logger.info("Explicit cloud routing enabled - using cloud proxy client")
            async for client in _cloud_client(config, timeout, workspace=workspace):
                yield client
            return

    # --- Per-project routing ---
    # Trigger: project_name provided without explicit routing override.
    # Why: project mode is the source of truth for project-scoped commands.
    # Outcome: route via project.mode (CLOUD/LOCAL).
    if project_name is not None and not _explicit_routing():
        project_mode = config.get_project_mode(project_name)
        if project_mode == ProjectMode.CLOUD:
            logger.info(f"Project '{project_name}' is cloud mode - using cloud proxy client")
            try:
                async for client in _cloud_client(config, timeout, workspace=workspace):
                    yield client
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Project '{project_name}' is set to cloud mode but no credentials found. "
                    "Run 'bm cloud set-key <key>' or 'bm cloud login' first."
                ) from exc
            return

        logger.info(f"Project '{project_name}' is local mode - using ASGI client")
        async with _asgi_client(timeout) as client:
            yield client
        return

    # --- Default fallback ---
    logger.info("Default routing - using ASGI client for local Basic Memory API")
    async with _asgi_client(timeout) as client:
        yield client


def create_client() -> AsyncClient:
    """Create an HTTP client based on explicit routing flags.

    DEPRECATED: Use get_client() context manager instead for proper resource management.
    """
    timeout = _build_timeout()

    if _force_local_mode() or not _force_cloud_mode():
        logger.info("Creating ASGI client for local Basic Memory API")
        return _asgi_client(timeout)

    logger.info("Creating HTTP client for cloud proxy (legacy create_client path)")
    config = ConfigManager().config
    proxy_base_url = f"{config.cloud_host}/proxy"
    return AsyncClient(base_url=proxy_base_url, timeout=timeout)
