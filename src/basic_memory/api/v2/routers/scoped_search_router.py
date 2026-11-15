"""Database-scoped search: one query over an explicit set of projects.

The caller names the projects. Nothing here discovers a scope, and an empty set
answers no rows; Cloud resolves authorization first and passes the effective ids.
The route shares the project route's reader, so one project here ranks exactly as
that project's own route does, and several projects are one ranking over the union
rather than per-project pages merged afterwards.
"""

import time
from collections.abc import Sequence

import logfire
from fastapi import APIRouter, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.api.v2.utils import (
    load_temporal_metadata,
    search_error_boundary,
    to_search_results,
)
from basic_memory.deps import AppConfigDep, SessionMakerDep
from basic_memory.models import Entity
from basic_memory.repository.search_repository import create_search_reader
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_index_freshness import semantic_index_current_as_of
from basic_memory.schemas.search import (
    MAX_SCOPED_SEARCH_PAGE_SIZE,
    ScopedSearchQuery,
    SearchResponse,
)
from basic_memory.services.scoped_search_service import ScopedSearchService

# App registration mounts this router at /v2.
router = APIRouter(tags=["search"])


# QUERY only: a search is a safe read with a body (RFC 9110 draft). OpenAPI
# cannot describe QUERY, so the route stays out of the schema.
@router.api_route(
    "/search/",
    methods=["QUERY"],
    response_model=SearchResponse,
    include_in_schema=False,
)
async def search_scope(
    query: ScopedSearchQuery,
    app_config: AppConfigDep,
    session_maker: SessionMakerDep,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_SCOPED_SEARCH_PAGE_SIZE),
) -> SearchResponse:
    """Search an explicit set of projects in this database.

    Results are one ranking across the whole scope and carry ``project_id`` and
    ``project_external_id``. Hydration reads only from projects in scope.
    """
    response.headers["Accept-Query"] = "application/json"
    # The read cache is keyed by one project's generation. A set of projects has no
    # single generation to invalidate on, so this route is never cached.
    response.headers["Cache-Control"] = "no-store"

    scope = ProjectScope.of(query.project_ids)
    service = ScopedSearchService(
        session_maker, scope, create_search_reader(session_maker, scope, app_config)
    )
    temporal_requested = query.has_temporal_filter()
    offset = (page - 1) * page_size

    with logfire.span(
        "api.request.search_scope",
        entrypoint="api",
        domain="search",
        action="search_scope",
        project_count=len(scope.project_ids),
        page=page,
        page_size=page_size,
        retrieval_mode=query.retrieval_mode.value,
        has_temporal_filter=temporal_requested,
    ):
        search_started = time.perf_counter()
        with search_error_boundary():
            # Why no exact count: a COUNT(*) over every match cost as much as the search
            #   on large scopes, and semantic modes would need a second retrieval to count.
            # Outcome: probe one row past the page and report a lower bound, marked
            #   inexact: rows at this offset prove offset + len(results) exist; an
            #   empty page proves nothing about the offset it skipped, so it reports 0.
            results = await service.search(query, limit=page_size + 1, offset=offset)
            has_more = len(results) > page_size
            results = results[:page_size]
            total = offset + len(results) if results else 0

        search_ms = _elapsed_ms(search_started)

        # Why one session for every read after the search: each session checks out a
        #   pooled connection and runs its own transaction, and on a remote database
        #   every one of those is a round trip. Outcome: project identities, valid
        #   time, entity hydration and the freshness check share one connection, one
        #   after another, never concurrently (a small pool could make a concurrent
        #   read wait behind another request). The search keeps its own session
        #   because its reader is shared with the per-project route. A page with no
        #   hits still answers the freshness check, on this same session.
        temporal_by_source = {}
        project_external_ids: dict[int, str] = {}
        async with db.scoped_session(session_maker) as session:
            hydrate_started = time.perf_counter()
            if results:
                project_external_ids = await service.project_external_ids(session, results)
                if temporal_requested:
                    temporal_by_source = await load_temporal_metadata(service, session, results)
            search_results = await to_search_results(
                _SessionEntityLookup(service, session),
                results,
                temporal_by_source=temporal_by_source,
                project_external_ids=project_external_ids,
            )
            hydrate_ms = _elapsed_ms(hydrate_started)

            freshness_started = time.perf_counter()
            current_as_of = await semantic_index_current_as_of(
                session_maker, app_config, scope, session
            )
            freshness_ms = _elapsed_ms(freshness_started)

        # Each step's server time, so a caller (or a host proxying this route) can see
        # which step is slow without access to tracing. Durations only, no data.
        response.headers["Server-Timing"] = (
            f"search;dur={search_ms:.1f}, "
            f"hydrate;dur={hydrate_ms:.1f}, "
            f"freshness;dur={freshness_ms:.1f}"
        )
        return SearchResponse(
            results=search_results,
            current_page=page,
            page_size=page_size,
            total=total,
            total_is_exact=False,
            has_more=has_more,
            # None, not False, when nothing was asked: an ordinary search payload stays
            # exactly what it was before valid time existed.
            temporal_applied=True if temporal_requested else None,
            semantic_index_current_as_of=current_as_of,
        )


def _elapsed_ms(started: float) -> float:
    """Milliseconds since a ``time.perf_counter()`` reading."""
    return (time.perf_counter() - started) * 1000


class _SessionEntityLookup:
    """Hydrate a page's entities on the route's session instead of a new one."""

    def __init__(self, service: ScopedSearchService, session: AsyncSession) -> None:
        self._service = service
        self._session = session

    async def get_entities_by_id(self, ids: list[int]) -> Sequence[Entity]:
        return await self._service.get_entities_by_id(ids, session=self._session)
