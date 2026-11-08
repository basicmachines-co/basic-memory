"""Route-level read caching on fastapi-redis-sdk, with versioned eviction groups.

fastapi-redis-sdk serves the HTTP side: hit short-circuit, response capture,
ETag/304, Cache-Control and the X-Redis-Cache header. This module supplies what it
leaves to the application:

- the key: tenant, project, user, workspace, the group's current version and the
  request itself, so invalidation is a version replacement instead of a key scan
- QUERY support, which the SDK's ``cache()`` bypasses (it caches GET only)
- rendered reads, whose stored entry is the route's full answer and whose response
  is rendered from it per request (a note slice, a resource's own media type)
- a no-op when the host has no cache configured

Attach with ``dependencies=[cached_read(...)]`` on a router built with
``route_class=ReadCacheRoute``. The route class serves the read before FastAPI solves
any dependency, so a hit returns before anything opens a database connection.

The SDK signals a hit by raising ``CacheHitException`` from its dependency. Raised
inside FastAPI's dependency solving, FastAPI instrumentation records it on the
request span as an exception, once per hit. ``ReadCacheRoute`` runs the SDK step
itself and catches the hit before the instrumented route code runs, so a hit is
returned as a response and never raised through the app.
"""

import importlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from hashlib import blake2b
from typing import Any, Literal, Protocol, override
from urllib.parse import urlencode

import logfire
from fastapi import Depends, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from opentelemetry import trace
from opentelemetry.trace import Span
from loguru import logger
from pydantic import TypeAdapter
from redis.exceptions import RedisError
from redis_fastapi.cache import CacheHitException, CachePending, cache
from redis_fastapi.config import CACHE_STATUS_HEADER

from basic_memory.read_cache.contract import (
    CONTENT,
    NAMES,
    PROJECT,
    WORKSPACE_ENTRIES_PROJECT_ID,
    NoteGroup,
    ReadCacheGroup,
    ReadCacheOperation,
    ReadCacheUnavailable,
    canonical_uuid,
    group_kind,
)
from basic_memory.read_cache.keys import entry_key, read_cache_request_digest
from basic_memory.read_cache.redis import EntryVersions, RedisReadCache
from basic_memory.telemetry import debug_span
from basic_memory.workspace_context import current_workspace_permalink_context

# The package re-exports a function named ``cache`` that shadows this submodule on
# attribute access. Its MAX_CACHEABLE_BODY_SIZE is the payload cap the app configures,
# read on every store so a rendered read honors the same cap as the SDK's capture.
_sdk_cache = importlib.import_module("redis_fastapi.cache")

_PENDING_STATE = "redis_cache_pending"
_SERVED_STATE = "basic_memory_read_cache_served"


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
type ProjectForRequest = Callable[[Request], str]
type RouteHandler = Callable[[Request], Coroutine[Any, Any, Response]]
type SdkCacheDependency = Callable[..., AsyncGenerator[None]]
type LookupOutcome = Literal["hit", "miss", "bypass", "unavailable"]


class RenderedRead(Protocol):
    """A cached read whose stored entry is not the response it sends.

    The entry holds the route's full answer once, and every request's response is
    rendered from it. Requests that differ only in ``params`` share one entry, so a
    note read with a token budget and one without do not each reach the database.
    """

    @property
    def params(self) -> frozenset[str]:
        """Query parameters that only shape the response, kept out of the key."""
        ...

    def check(self, request: Request) -> None:
        """Raise ValueError when ``params`` are invalid, so the route answers them."""
        ...

    def store(self, response: Response) -> bytes:
        """The entry to store for the route's full answer."""
        ...

    def render(self, stored: bytes, request: Request) -> Response:
        """This request's response, rendered from a stored entry."""
        ...


_read_cache_lookups = logfire.metric_counter(
    "read_cache.lookups",
    unit="1",
    description="Cached-read lookups by operation, group kind and outcome",
)


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


@dataclass(frozen=True, slots=True, eq=False)
class _ReadThrough:
    """One route's read cache, served by ``ReadCacheRoute`` around the endpoint.

    ``eq=False`` keeps identity hashing: FastAPI caches dependencies by their
    callable, and ``hit_headers`` is a dict.
    """

    operation: ReadCacheOperation
    group: GroupForRequest
    project: ProjectForRequest
    ttl_seconds: int
    hit_headers: Mapping[str, str]
    sdk_dependency: SdkCacheDependency
    rendered: RenderedRead | None

    async def __call__(self, request: Request) -> None:
        """Fail fast when the route was built without ``ReadCacheRoute``.

        Without the route class nothing would serve or fill the cache, and the
        route would silently run uncached.
        """
        if not getattr(request.state, _SERVED_STATE, False):
            raise TypeError("cached_read routes need a router with route_class=ReadCacheRoute")

    async def serve(self, request: Request, endpoint: RouteHandler) -> Response:
        """Answer from the cache, or run the endpoint and let the SDK store its response."""
        setattr(request.state, _SERVED_STATE, True)
        target = await _cached_target(request, self.group, self.project, self.rendered)
        if target is None:
            return await endpoint(request)

        request.state.basic_memory_read_cache_target = target
        steps = self.sdk_dependency(
            _sdk_request(request, rendered=self.rendered is not None),
            target.scope.cache.client,
        )
        # The lookup outcome lives on the request span (FastAPI's route span when
        # the app is instrumented) and in a counter. The step span below is debug
        # only, so a host exporting at info still sees every outcome without
        # paying for one extra record per cached read.
        lookup = _LookupRecord(
            request_span=trace.get_current_span(),
            operation=self.operation.value,
            group=group_kind(target.group),
        )
        lookup.request_span.set_attributes(
            {
                "cache.operation": lookup.operation,
                "cache.group": lookup.group,
                "cache.configured_ttl_seconds": self.ttl_seconds,
            }
        )
        with debug_span("read_cache.read_through", operation=lookup.operation):
            try:
                await anext(steps)
            except CacheHitException as hit:
                # The SDK raises on a hit. It is caught here, before FastAPI solves
                # dependencies, so instrumentation never records a hit as an error.
                lookup.record("hit")
                response = hit.response
                if self.rendered is not None:
                    response = _with_validators(
                        request,
                        self.rendered.render(bytes(response.body), request),
                        cache_status="HIT",
                        cache_control=response.headers["cache-control"],
                    )
                response.headers.update(self.hit_headers)
                return response
            except ReadCacheUnavailable:
                # Version lookup failed: Redis is down, so the database answers
                # and nothing is stored under a version we could not read.
                lookup.record("unavailable")
                skip_read_cache_store(request)
            else:
                lookup.record(
                    await _track_pending_entry(request, target, ttl_seconds=self.ttl_seconds)
                )
        try:
            if self.rendered is None:
                return await endpoint(request)
            return await self._fill_rendered(request, endpoint, self.rendered)
        finally:
            await steps.aclose()

    async def _fill_rendered(
        self, request: Request, endpoint: RouteHandler, rendered: RenderedRead
    ) -> Response:
        """Run the route for its full answer, store that, and render this request's response.

        The SDK's capture middleware stores the bytes it sends, which here are the
        rendered response, so the full answer is stored before rendering and the
        middleware is told to store nothing.
        """
        pending: CachePending | None = getattr(request.state, _PENDING_STATE, None)
        skip_read_cache_store(request)
        response = await endpoint(_without_query_params(request, rendered.params))
        if not 200 <= response.status_code < 300:
            return response
        stored = rendered.store(response)
        if pending is not None and len(stored) <= _sdk_cache.MAX_CACHEABLE_BODY_SIZE:
            await _store_entry(pending, stored)
        return _with_validators(
            request,
            rendered.render(stored, request),
            cache_status="MISS" if pending is not None else None,
            cache_control=f"max-age={self.ttl_seconds}",
        )


class ReadCacheRoute(APIRoute):
    """An API route that serves its ``cached_read`` before solving dependencies.

    Routes without a ``cached_read`` dependency get FastAPI's handler unchanged.
    """

    @override
    def get_route_handler(self) -> RouteHandler:
        endpoint = super().get_route_handler()
        read_through = next(
            (
                depends.dependency
                for depends in self.dependencies
                if isinstance(depends.dependency, _ReadThrough)
            ),
            None,
        )
        if read_through is None:
            return endpoint

        async def handler(request: Request) -> Response:
            return await read_through.serve(request, endpoint)

        return handler


def cached_read(
    operation: ReadCacheOperation,
    *,
    group: GroupForRequest,
    ttl_seconds: int,
    hit_headers: Mapping[str, str] | None = None,
    project: ProjectForRequest | None = None,
    rendered: RenderedRead | None = None,
) -> Any:
    """A route dependency that marks the route's reads as cached under ``operation``.

    The router must use ``route_class=ReadCacheRoute``, which does the serving.

    ``project`` names the project whose versions key the entry: by default the
    ``project_id`` path parameter; ``workspace_project`` for workspace-wide reads.

    ``hit_headers`` are response headers the route always sets. A hit returns before
    the route runs, and fastapi-redis-sdk 0.8.1 does not replay stored headers, so
    they are added to every cached response here.

    ``rendered`` stores the route's full answer and renders each response from it;
    see ``RenderedRead``.
    """

    async def key_builder(request: Request, eviction_group: str = "", prefix: str = "") -> str:
        target: _CachedTarget = request.state.basic_memory_read_cache_target
        versions = await target.scope.cache.versions(target.project_id, target.group)
        key = entry_key(
            prefix=target.scope.cache.prefix,
            namespace=target.scope.cache.namespace,
            project_id=target.project_id,
            operation=operation.value,
            versions=versions.token,
            request_digest=read_cache_request_digest(target.scope.user_key, target.identity),
        )
        # A miss stores under this key, and invalidation can delete it only if it
        # was recorded under the versions it embeds.
        request.state.basic_memory_read_cache_entry = _LookedUpEntry(versions=versions, key=key)
        return key

    return Depends(
        _ReadThrough(
            operation=operation,
            group=group,
            project=project or path_project,
            ttl_seconds=ttl_seconds,
            hit_headers=dict(hit_headers or {}),
            sdk_dependency=cache(ttl=ttl_seconds, key_builder=key_builder, cache_prefix=""),
            rendered=rendered,
        )
    )


def _sdk_request(request: Request, *, rendered: bool) -> Request:
    """The request as the SDK should see it.

    A rendered read's validators belong to its rendered response, not to the stored
    entry the SDK compares them with, so the SDK never sees ``If-None-Match``.
    """
    scope = request.scope
    if rendered:
        scope = {
            **scope,
            "headers": [
                (name, value) for name, value in scope["headers"] if name != b"if-none-match"
            ],
        }
    if request.method == "QUERY":
        return _AsGet(scope, request.receive)
    return Request(scope, request.receive) if rendered else request


def _without_query_params(request: Request, names: frozenset[str]) -> Request:
    """The same request without ``names`` in its query string.

    The copy shares the request's state, so the cache target and pending entry the
    lookup recorded stay visible to the route.
    """
    if not names & request.query_params.keys():
        return request
    query = urlencode(
        [(name, value) for name, value in request.query_params.multi_items() if name not in names]
    )
    return Request({**request.scope, "query_string": query.encode("latin-1")}, request.receive)


async def _store_entry(pending: CachePending, stored: bytes) -> None:
    """Write one entry in the SDK's format, so its hit path can serve it.

    The SDK's capture middleware writes the same two fields. A failed write leaves
    the read uncached, as a failed capture does.
    """
    entry = {
        "body": stored.decode(errors="replace"),
        "etag": _etag(stored),
    }
    try:
        await pending.redis.set(pending.key, json.dumps(entry), ex=pending.ttl)
    except (RedisError, OSError):  # pragma: no cover - needs a Redis failure mid-request
        logger.warning("Read cache store failed; serving uncached", key=pending.key)


def _etag(body: bytes) -> str:
    return f'W/"{blake2b(body, digest_size=16).hexdigest()}"'


def _with_validators(
    request: Request, response: Response, *, cache_status: str | None, cache_control: str
) -> Response:
    """Add the validators the SDK adds to stored responses, and answer a revalidation."""
    headers = {"ETag": _etag(bytes(response.body)), "Cache-Control": cache_control}
    if cache_status is not None:
        headers[CACHE_STATUS_HEADER] = cache_status
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


async def _track_pending_entry(
    request: Request, target: "_CachedTarget", ttl_seconds: int
) -> LookupOutcome:
    """Record the entry a miss is about to store, or keep the response uncached."""
    # The SDK bypasses requests that send ``Cache-Control: no-store`` without
    # building a key, so there is nothing to store and nothing to record.
    if not hasattr(request.state, _PENDING_STATE):
        return "bypass"
    looked_up: _LookedUpEntry = request.state.basic_memory_read_cache_entry
    try:
        await target.scope.cache.track_entry(
            target.project_id, target.group, looked_up.versions, looked_up.key, ttl_seconds
        )
    except ReadCacheUnavailable:
        # An entry invalidation cannot find would hold its memory for the whole
        # TTL, so an untracked response is served uncached.
        skip_read_cache_store(request)
        return "unavailable"
    return "miss"


@dataclass(frozen=True, slots=True)
class _LookupRecord:
    """Where one cached read reports its lookup outcome."""

    request_span: Span
    operation: str
    group: str

    def record(self, outcome: LookupOutcome) -> None:
        self.request_span.set_attribute("cache.lookup.outcome", outcome)
        # No ids in the labels: operation and group kind are small fixed sets.
        _read_cache_lookups.add(
            1,
            {"cache.operation": self.operation, "cache.group": self.group, "outcome": outcome},
        )


@dataclass(frozen=True, slots=True)
class _LookedUpEntry:
    """The key a lookup used and the versions it embeds."""

    versions: EntryVersions
    key: str


@dataclass(frozen=True, slots=True)
class _CachedTarget:
    scope: ReadCacheScope
    project_id: str
    group: ReadCacheGroup
    identity: str


async def _cached_target(
    request: Request,
    group: GroupForRequest,
    project: ProjectForRequest,
    rendered: RenderedRead | None,
) -> _CachedTarget | None:
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
        project_id = canonical_uuid(project(request), label="project id")
        resolved_group = group(request)
        presentation_params = frozenset[str]()
        if rendered is not None:
            rendered.check(request)
            presentation_params = rendered.params
        identity = await _request_identity(request, excluded=presentation_params)
    except ValueError:
        # Malformed ids or body: the route answers with an error, never cached.
        return None
    return _CachedTarget(
        scope=scope,
        project_id=project_id,
        group=resolved_group,
        identity=identity,
    )


async def _request_identity(request: Request, *, excluded: frozenset[str]) -> str:
    """Everything besides tenant, user and versions that shapes the stored answer.

    Only the query parameters the route declares go into the key, serialized as
    JSON so values can never run together (``a=b%26c=d`` is not ``a=b&c=d``), and
    undeclared parameters (a cache-busting nonce) cannot multiply entries. A QUERY
    body is keyed by its validated model for the same reason: fields the model
    ignores, key order and spelled-out defaults do not change the response.
    ``excluded`` parameters only shape a rendered read's response, not its entry.
    """
    body = None
    if request.method == "QUERY":
        raw = await request.body()
        if raw:
            body = _validated_body(request, json.loads(raw))
    query = {
        name: request.query_params.getlist(name)
        for name in sorted(_declared_query_params(request) - excluded)
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
    body_params = [
        param
        for dependant in _route_dependencies(_api_route(request).dependant)
        for param in dependant.body_params
    ]
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
    return frozenset(
        param.alias
        for dependant in _route_dependencies(_api_route(request).dependant)
        for param in dependant.query_params
    )


def _route_dependencies(dependant: Dependant) -> Iterator[Dependant]:
    """Include nested dependencies so their inputs cannot collide in cache keys.

    FastAPI no longer exposes get_flat_dependant; walk the route's dependency
    tree directly instead of constructing a synthetic flattened dependant.
    """
    yield dependant
    for dependency in dependant.dependencies:
        yield from _route_dependencies(dependency)


# --- Projects by route ---


def path_project(request: Request) -> str:
    """The project named in the route's path."""
    return request.path_params["project_id"]


def workspace_project(request: Request) -> str:
    """Workspace-wide reads, kept under the reserved workspace entries id."""
    return WORKSPACE_ENTRIES_PROJECT_ID


# --- Groups by route ---


def note_group(request: Request) -> ReadCacheGroup:
    return NoteGroup(request.path_params["entity_id"])


def content_group(request: Request) -> ReadCacheGroup:
    return CONTENT


def names_group(request: Request) -> ReadCacheGroup:
    return NAMES


def project_group(request: Request) -> ReadCacheGroup:
    return PROJECT


__all__ = [
    "ReadCacheRoute",
    "ReadCacheScope",
    "ReadCacheScopeResolver",
    "RenderedRead",
    "cached_read",
    "content_group",
    "names_group",
    "note_group",
    "path_project",
    "project_group",
    "skip_read_cache_store",
    "workspace_project",
]
