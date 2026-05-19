import os
from asyncio import Lock
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Callable, Optional

from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

import logfire
from basic_memory.config import ConfigManager, ProjectMode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

LocalDatabaseState = tuple["AsyncEngine", "async_sessionmaker[AsyncSession]"]
_MISSING_STATE_VALUE = object()


@dataclass
class _PreparedLocalAsgiDatabase:
    active_count: int
    previous_engine: object
    previous_session_maker: object
    dependency_context: AbstractAsyncContextManager[LocalDatabaseState]


_prepared_local_asgi_database_lock = RLock()
_prepared_local_asgi_database_prepare_locks: dict[FastAPI, Lock] = {}
_prepared_local_asgi_databases: dict[FastAPI, _PreparedLocalAsgiDatabase] = {}


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


def _build_asgi_client(app: FastAPI, timeout: Timeout) -> AsyncClient:
    """Create a local ASGI client for an already-prepared FastAPI app."""
    from basic_memory.workspace_context import workspace_permalink_headers

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=timeout,
        # Local ASGI calls still cross the HTTP boundary, so request handlers need
        # the same workspace permalink metadata that cloud proxy calls receive.
        headers=workspace_permalink_headers(),
    )


def _get_prepared_local_asgi_database_prepare_lock(app: FastAPI) -> Lock:
    """Get the async lock that serializes first-time DB preparation for an app."""
    with _prepared_local_asgi_database_lock:
        prepare_lock = _prepared_local_asgi_database_prepare_locks.get(app)
        if prepare_lock is None:
            prepare_lock = Lock()
            _prepared_local_asgi_database_prepare_locks[app] = prepare_lock
        return prepare_lock


@asynccontextmanager
async def _resolve_local_asgi_database(app: FastAPI) -> AsyncIterator[LocalDatabaseState]:
    """Resolve database state for a local ASGI request."""
    from fastapi.dependencies.utils import get_dependant, solve_dependencies

    from basic_memory.deps import get_engine_factory

    async def resolve_database_state(
        database_state: Annotated[LocalDatabaseState, Depends(get_engine_factory)],
    ) -> LocalDatabaseState:
        return database_state

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
        "path_params": {},
    }

    async with AsyncExitStack() as request_stack, AsyncExitStack() as function_stack:
        scope["fastapi_inner_astack"] = request_stack
        scope["fastapi_function_astack"] = function_stack
        request = Request(scope)
        dependant = get_dependant(path="/", call=resolve_database_state)
        solved = await solve_dependencies(
            request=request,
            dependant=dependant,
            dependency_overrides_provider=app,
            async_exit_stack=request_stack,
            embed_body_fields=False,
        )
        if solved.errors:
            raise RuntimeError(f"Failed to resolve local ASGI database dependency: {solved.errors}")

        yield await resolve_database_state(**solved.values)


def _retain_prepared_local_asgi_database(app: FastAPI) -> bool:
    """Retain an active local ASGI database preparation if one exists."""
    with _prepared_local_asgi_database_lock:
        active = _prepared_local_asgi_databases.get(app)
        if active is None:
            return False

        active.active_count += 1
        return True


def _install_prepared_local_asgi_database(
    app: FastAPI,
    database_state: LocalDatabaseState,
    dependency_context: AbstractAsyncContextManager[LocalDatabaseState],
) -> None:
    """Install local ASGI database state after dependency resolution."""
    with _prepared_local_asgi_database_lock:
        active = _prepared_local_asgi_databases.get(app)
        if active is not None:
            raise RuntimeError("Local ASGI database state installed while another state is active")

        previous_engine = getattr(app.state, "engine", _MISSING_STATE_VALUE)
        previous_session_maker = getattr(app.state, "session_maker", _MISSING_STATE_VALUE)
        engine, session_maker = database_state

        app.state.engine = engine
        app.state.session_maker = session_maker
        _prepared_local_asgi_databases[app] = _PreparedLocalAsgiDatabase(
            active_count=1,
            previous_engine=previous_engine,
            previous_session_maker=previous_session_maker,
            dependency_context=dependency_context,
        )


def _restore_local_asgi_state_attribute(app: FastAPI, name: str, previous_value: object) -> None:
    """Restore a FastAPI app.state attribute captured before local ASGI preparation."""
    if previous_value is _MISSING_STATE_VALUE:
        if hasattr(app.state, name):
            delattr(app.state, name)
    else:
        setattr(app.state, name, previous_value)


def _release_prepared_local_asgi_database(
    app: FastAPI,
) -> AbstractAsyncContextManager[LocalDatabaseState] | None:
    """Release local ASGI database state after a client context exits."""
    with _prepared_local_asgi_database_lock:
        active = _prepared_local_asgi_databases.get(app)
        if active is None:
            raise RuntimeError("Local ASGI database state released without a matching retain")

        active.active_count -= 1
        if active.active_count > 0:
            return None

        del _prepared_local_asgi_databases[app]
        _restore_local_asgi_state_attribute(app, "engine", active.previous_engine)
        _restore_local_asgi_state_attribute(
            app,
            "session_maker",
            active.previous_session_maker,
        )
        return active.dependency_context


@asynccontextmanager
async def _prepared_local_asgi_database(app: FastAPI) -> AsyncIterator[None]:
    """Initialize local ASGI database state before the first request."""
    prepare_lock = _get_prepared_local_asgi_database_prepare_lock(app)
    async with prepare_lock:
        if not _retain_prepared_local_asgi_database(app):
            database_context = _resolve_local_asgi_database(app)
            database_state = await database_context.__aenter__()
            try:
                _install_prepared_local_asgi_database(app, database_state, database_context)
            except Exception:
                await database_context.__aexit__(None, None, None)
                raise

    try:
        yield
    finally:
        database_context = _release_prepared_local_asgi_database(app)
        if database_context is not None:
            await database_context.__aexit__(None, None, None)


@asynccontextmanager
async def _asgi_client(timeout: Timeout) -> AsyncIterator[AsyncClient]:
    """Create a local ASGI client."""
    # Import on first local-client use so CLI help/version paths can import
    # routing helpers without constructing the full FastAPI router graph.
    from basic_memory.api.app import app as fastapi_app

    # Trigger: local ASGITransport does not execute FastAPI lifespan startup.
    # Why: letting request dependencies initialize Postgres can run asyncpg DDL
    # under Starlette's request loop and trigger CPython's empty-ready-queue race.
    # Outcome: request handling sees the same app.state database objects as API
    # lifespan startup would have provided.
    async with _prepared_local_asgi_database(fastapi_app):
        async with _build_asgi_client(fastapi_app, timeout) as client:
            yield client


async def _resolve_cloud_token(config) -> str:
    """Resolve cloud token with API key preferred, OAuth fallback."""
    with logfire.span(
        "routing.resolve_cloud_credentials",
        has_api_key=bool(config.cloud_api_key),
    ):
        token = config.cloud_api_key
        if token:
            return token

        from basic_memory.cli.auth import CLIAuth

        auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
        token = await auth.get_valid_token()
        if token:
            return token

        logger.error("Cloud routing requested but no credentials were available")
        raise RuntimeError(
            "Cloud routing requested but no credentials found. "
            "Run 'bm cloud api-key save <key>' or 'bm cloud login' first."
        )


def resolve_configured_workspace(
    *,
    config=None,
    project_name: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Optional[str]:
    """Resolve workspace from explicit input, per-project config, then global default."""
    if workspace is not None:
        return workspace

    if config is None:
        config = ConfigManager().config

    if project_name is not None:
        project_entry = config.projects.get(project_name)
        if project_entry and project_entry.workspace_id:
            return project_entry.workspace_id

    return config.default_workspace


@asynccontextmanager
async def _cloud_client(
    config,
    timeout: Timeout,
    workspace: Optional[str] = None,
) -> AsyncIterator[AsyncClient]:
    """Create a cloud proxy client with resolved credentials."""
    from basic_memory.workspace_context import workspace_permalink_headers

    token = await _resolve_cloud_token(config)
    proxy_base_url = f"{config.cloud_host}/proxy"
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(workspace_permalink_headers())
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
async def get_cloud_control_plane_client(
    workspace: Optional[str] = None,
) -> AsyncIterator[AsyncClient]:
    """Create a control-plane cloud client for endpoints outside /proxy."""
    config = ConfigManager().config
    timeout = _build_timeout()
    token = await _resolve_cloud_token(config)
    headers = {"Authorization": f"Bearer {token}"}
    if workspace:
        headers["X-Workspace-ID"] = workspace
    logger.info(f"Creating HTTP client for cloud control plane at: {config.cloud_host}")
    async with AsyncClient(
        base_url=config.cloud_host,
        headers=headers,
        timeout=timeout,
    ) as client:
        yield client


# Optional factory override for dependency injection.
# The factory accepts an optional workspace keyword argument so that MCP tools
# can route individual requests to a different workspace than the one set at
# connection time.  See basic-memory-cloud main.py tenant_asgi_client_factory.
_client_factory: Optional[Callable[..., AbstractAsyncContextManager[AsyncClient]]] = None


def set_client_factory(factory: Callable[..., AbstractAsyncContextManager[AsyncClient]]) -> None:
    """Override the default client factory (for cloud app, testing, etc)."""
    global _client_factory
    _client_factory = factory


def is_factory_mode() -> bool:
    """Return True when a client factory override is active (e.g., cloud app)."""
    return _client_factory is not None


@asynccontextmanager
async def get_cloud_proxy_client(
    workspace: Optional[str] = None,
) -> AsyncIterator[AsyncClient]:
    """Create a cloud proxy client for project-level operations.

    Used by MCP tools to fetch cloud project lists independently of the
    default get_client() routing, which always goes through the local ASGI
    transport in stdio mode.
    """
    config = ConfigManager().config
    timeout = _build_timeout()
    async with _cloud_client(config, timeout, workspace=workspace) as client:
        yield client


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
        async with _client_factory(workspace=workspace) as client:
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
            logger.debug("Explicit local routing enabled - using ASGI client")
            async with _asgi_client(timeout) as client:
                yield client
            return

        if _force_cloud_mode():
            logger.debug("Explicit cloud routing enabled - using cloud proxy client")
            effective_workspace = resolve_configured_workspace(
                config=config,
                project_name=project_name,
                workspace=workspace,
            )
            async with _cloud_client(config, timeout, workspace=effective_workspace) as client:
                yield client
            return

    # --- Per-project routing ---
    # Trigger: project_name provided without explicit routing override.
    # Why: project mode is the source of truth for project-scoped commands.
    # Outcome: route via project.mode (CLOUD/LOCAL).
    if project_name is not None and not _explicit_routing():
        project_mode = config.get_project_mode(project_name)
        if project_mode == ProjectMode.CLOUD:
            logger.debug(f"Project '{project_name}' is cloud mode - using cloud proxy client")
            effective_workspace = resolve_configured_workspace(
                config=config,
                project_name=project_name,
                workspace=workspace,
            )
            try:
                async with _cloud_client(config, timeout, workspace=effective_workspace) as client:
                    yield client
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Project '{project_name}' is set to cloud mode but no credentials found. "
                    "Run 'bm cloud api-key save <key>' or 'bm cloud login' first."
                ) from exc
            return

        logger.debug(f"Project '{project_name}' is local mode - using ASGI client")
        async with _asgi_client(timeout) as client:
            yield client
        return

    # --- Default fallback ---
    logger.debug("Default routing - using ASGI client for local Basic Memory API")
    async with _asgi_client(timeout) as client:
        yield client


def create_client() -> AsyncClient:
    """Create an HTTP client based on explicit routing flags.

    DEPRECATED: Use get_client() context manager instead for proper resource management.
    """
    timeout = _build_timeout()

    if _force_local_mode() or not _force_cloud_mode():
        logger.info("Creating ASGI client for local Basic Memory API")
        # Deprecated sync path: create_client() cannot await the local ASGI
        # pre-initialization used by get_client(), so callers that need proper
        # resource setup should use the async context manager instead.
        from basic_memory.api.app import app as fastapi_app

        return _build_asgi_client(fastapi_app, timeout)

    logger.info("Creating HTTP client for cloud proxy (legacy create_client path)")
    config = ConfigManager().config
    proxy_base_url = f"{config.cloud_host}/proxy"
    return AsyncClient(base_url=proxy_base_url, timeout=timeout)
