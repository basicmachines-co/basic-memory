"""Route-level read caching on fastapi-redis-sdk, with versioned eviction groups.

fastapi-redis-sdk serves the HTTP side: hit short-circuit, response capture,
ETag/304, Cache-Control and the X-Redis-Cache header. This module supplies what it
leaves to the application:

- the key: tenant, project, user, workspace, the group's current version and the
  request itself, so invalidation is a version replacement instead of a key scan
- QUERY support, which the SDK's ``cache()`` bypasses (it caches GET only)
- a no-op when the host has no cache configured

Attach with ``dependencies=[cached_read(...)]``. Route-level dependencies resolve
before the endpoint's own parameters, so a hit returns before any dependency that
opens a database connection.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, override

import logfire
from fastapi import Depends, Request
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from redis_fastapi.cache import CacheHitException, cache

from basic_memory.read_cache.contract import (
    CONTENT,
    PROJECT,
    NoteGroup,
    ReadCacheGroup,
    ReadCacheOperation,
    ReadCacheUnavailable,
    canonical_uuid,
    group_kind,
)
from basic_memory.read_cache.keys import entry_key, read_cache_request_digest
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.workspace_context import current_workspace_permalink_context

_PENDING_STATE = "redis_cache_pending"


@dataclass(frozen=True, slots=True)
class ReadCacheScope:
    """The cache serving one request, and whose view of the project it caches.

    ``user_key`` separates entries per user. A single-user standalone host
    passes an empty string.
    """

    cache: RedisReadCache
    user_key: str


type ReadCacheScopeResolver = Callable[[Request], Awaitable[ReadCacheScope | None]]
type GroupForRequest = Callable[[Request], ReadCacheGroup]


async def _resolve_scope(request: Request) -> ReadCacheScope | None:
    """Ask the host which cache serves this request.

    A host sets ``app.state.read_cache_scope_resolver``; a managed host binds it to
    the authenticated tenant and user. Without one, the standalone container's
    cache is used for its single local user.
    """
    resolver: ReadCacheScopeResolver | None = getattr(
        request.app.state, "read_cache_scope_resolver", None
    )
    if resolver is not None:
        return await resolver(request)

    container = getattr(request.app.state, "container", None)
    if container is None:
        # Deferred import avoids api.app -> container -> read_cache circular imports.
        from basic_memory.api.container import resolve_container

        container = resolve_container()
    cache_backend: RedisReadCache | None = container.read_cache
    if cache_backend is None:
        return None
    return ReadCacheScope(cache=cache_backend, user_key="")


def skip_read_cache_store(request: Request) -> None:
    """Keep this one response out of the cache.

    For answers that depend on more than their group: a fuzzy name match can
    change with any note's content, but resolve entries are retired only when
    names change.
    """
    if hasattr(request.state, _PENDING_STATE):
        delattr(request.state, _PENDING_STATE)


class _AsGet(Request):
    """The same request, reported to the SDK as a GET.

    QUERY is a safe, cacheable method (RFC 9110 draft), but the SDK's ``cache()``
    caches GET only. The view shares the request's scope, so the pending entry
    the SDK records reaches the capture middleware unchanged.
    """

    @property
    @override
    def method(self) -> str:
        return "GET"


def cached_read(
    operation: ReadCacheOperation,
    *,
    group: GroupForRequest,
    ttl_seconds: int,
    hit_headers: Mapping[str, str] | None = None,
) -> Any:
    """A route dependency that serves and fills the read cache for ``operation``.

    ``hit_headers`` are response headers the route always sets. A hit returns before
    the route runs, and fastapi-redis-sdk 0.8.1 does not replay stored headers, so
    they are added to every cached response here.
    """

    async def key_builder(request: Request, eviction_group: str = "", prefix: str = "") -> str:
        target: _CachedTarget = request.state.basic_memory_read_cache_target
        versions = await target.scope.cache.versions(target.project_id, target.group)
        return entry_key(
            prefix=target.scope.cache.prefix,
            namespace=target.scope.cache.namespace,
            project_id=target.project_id,
            operation=operation.value,
            versions=versions,
            request_digest=read_cache_request_digest(target.scope.user_key, target.identity),
        )

    sdk_dependency = cache(ttl=ttl_seconds, key_builder=key_builder, cache_prefix="")

    async def dependency(request: Request) -> AsyncIterator[None]:
        target = await _cached_target(request, group)
        if target is None:
            yield
            return

        request.state.basic_memory_read_cache_target = target
        sdk_request = (
            _AsGet(request.scope, request.receive) if request.method == "QUERY" else request
        )
        steps = sdk_dependency(sdk_request, target.scope.cache.client)
        with logfire.span(
            "read_cache.read_through",
            operation=operation.value,
        ) as span:
            span.set_attributes(
                {
                    "cache.operation": operation.value,
                    "cache.group": group_kind(target.group),
                    "cache.configured_ttl_seconds": ttl_seconds,
                }
            )
            try:
                # Raises the SDK's CacheHitException on a hit; its handler turns
                # it into the cached response before the endpoint runs.
                await anext(steps)
            except CacheHitException as hit:
                span.set_attribute("cache.lookup.outcome", "hit")
                hit.response.headers.update(hit_headers or {})
                raise
            except ReadCacheUnavailable:
                # Version lookup failed: Redis is down, so the database answers
                # and nothing is stored under a version we could not read.
                span.set_attribute("cache.lookup.outcome", "unavailable")
                skip_read_cache_store(request)
                target = None
            else:
                span.set_attribute("cache.lookup.outcome", "miss")
        if target is None:
            yield
            return
        try:
            yield
        finally:
            await steps.aclose()

    return Depends(dependency)


@dataclass(frozen=True, slots=True)
class _CachedTarget:
    scope: ReadCacheScope
    project_id: str
    group: ReadCacheGroup
    identity: str


async def _cached_target(request: Request, group: GroupForRequest) -> _CachedTarget | None:
    """Everything the key needs, or ``None`` when this request bypasses the cache."""
    if request.method not in {"GET", "QUERY"}:
        return None
    # A ranged read is sliced from the full body by the route; the key has no
    # range in it, so such a request must neither serve nor store an entry.
    if "range" in request.headers:
        return None
    scope = await _resolve_scope(request)
    if scope is None:
        return None
    try:
        project_id = canonical_uuid(request.path_params["project_id"], label="project id")
        resolved_group = group(request)
        identity = await _request_identity(request)
    except ValueError:
        # Malformed ids or body: the route answers with an error, never cached.
        return None
    return _CachedTarget(
        scope=scope,
        project_id=project_id,
        group=resolved_group,
        identity=identity,
    )


async def _request_identity(request: Request) -> str:
    """Everything besides tenant, user and versions that shapes the response.

    Only the query parameters the route declares go into the key, serialized as
    JSON so values can never run together (``a=b%26c=d`` is not ``a=b&c=d``), and
    undeclared parameters (a cache-busting nonce) cannot multiply entries. A QUERY
    body is keyed by its validated model for the same reason: fields the model
    ignores, key order and spelled-out defaults do not change the response.
    """
    body = None
    if request.method == "QUERY":
        raw = await request.body()
        if raw:
            body = _validated_body(request, json.loads(raw))
    query = {
        name: request.query_params.getlist(name)
        for name in sorted(_declared_query_params(request))
        if name in request.query_params
    }
    workspace = current_workspace_permalink_context()
    return read_cache_request_digest(
        json.dumps(
            {
                "path": request.url.path,
                "query": query,
                "body": body,
                "workspace": [
                    workspace.workspace_slug if workspace else "",
                    workspace.workspace_type if workspace else "",
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _validated_body(request: Request, raw_body: object) -> object:
    """The QUERY body as the route will see it, raising ValueError when invalid."""
    body_params = get_flat_dependant(_api_route(request).dependant).body_params
    if len(body_params) != 1:
        raise TypeError("cached QUERY routes take exactly one body model")
    adapter = _body_adapter(body_params[0].field_info.annotation)
    # A pydantic ValidationError is a ValueError, so an invalid body bypasses
    # the cache and the route answers it with its own 422.
    return adapter.dump_python(adapter.validate_python(raw_body), mode="json")


# One adapter per body model, built on the first request that needs it.
@lru_cache(maxsize=32)
def _body_adapter(annotation: Any) -> TypeAdapter[Any]:
    return TypeAdapter(annotation)


def _api_route(request: Request) -> APIRoute:
    route = request.scope["route"]
    if not isinstance(route, APIRoute):
        raise TypeError("cached_read is only valid on FastAPI API routes")
    return route


def _declared_query_params(request: Request) -> frozenset[str]:
    """Query parameter names the matched route and its dependencies read."""
    dependant = get_flat_dependant(_api_route(request).dependant)
    return frozenset(param.alias for param in dependant.query_params)


# --- Groups by route ---


def note_group(request: Request) -> ReadCacheGroup:
    return NoteGroup(request.path_params["entity_id"])


def content_group(request: Request) -> ReadCacheGroup:
    return CONTENT


def project_group(request: Request) -> ReadCacheGroup:
    return PROJECT


__all__ = [
    "ReadCacheScope",
    "ReadCacheScopeResolver",
    "cached_read",
    "content_group",
    "note_group",
    "project_group",
    "skip_read_cache_store",
]
