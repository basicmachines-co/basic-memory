"""Database-scoped search: one query over an explicit set of projects.

The caller names the projects. Nothing here discovers a scope, and an empty set
answers no rows; Cloud resolves authorization first and passes the effective ids.
The route shares the project route's reader, so one project here ranks exactly as
that project's own route does, and several projects are one ranking over the union
rather than per-project pages merged afterwards.
"""

import asyncio

import logfire
from fastapi import APIRouter, Query, Response

from basic_memory import db
from basic_memory.api.v2.utils import (
    load_temporal_metadata,
    search_error_boundary,
    to_search_results,
)
from basic_memory.deps import AppConfigDep, SessionMakerDep
from basic_memory.repository.search_repository import create_search_reader
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import ScopedSearchQuery, SearchResponse, SearchRetrievalMode
from basic_memory.services.scoped_search_service import ScopedSearchService

# App registration mounts this router at /v2.
router = APIRouter(tags=["search"])


@router.api_route(
    "/search/",
    methods=["QUERY"],
    response_model=SearchResponse,
    include_in_schema=False,
)
@router.post("/search/", response_model=SearchResponse)
async def search_scope(
    query: ScopedSearchQuery,
    app_config: AppConfigDep,
    session_maker: SessionMakerDep,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
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
    exact_count_available = query.retrieval_mode == SearchRetrievalMode.FTS
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
        with search_error_boundary():
            if exact_count_available:
                results, total = await asyncio.gather(
                    service.search(query, limit=page_size, offset=offset),
                    service.count(query),
                )
                has_more = offset + len(results) < total
            else:
                # Trigger: semantic modes would need another vector or hybrid pass to count.
                # Why: a search should not pay for a second semantic retrieval.
                # Outcome: probe one row past the page, leave total at 0, mark it inexact.
                results = await service.search(query, limit=page_size + 1, offset=offset)
                total = 0
                has_more = len(results) > page_size
                results = results[:page_size]

        temporal_by_source = {}
        project_external_ids: dict[int, str] = {}
        if results:
            async with db.scoped_session(session_maker) as session:
                project_external_ids = await service.project_external_ids(session, results)
                if temporal_requested:
                    temporal_by_source = await load_temporal_metadata(service, session, results)
        search_results = await to_search_results(
            service,
            results,
            temporal_by_source=temporal_by_source,
            project_external_ids=project_external_ids,
        )
        return SearchResponse(
            results=search_results,
            current_page=page,
            page_size=page_size,
            total=total,
            total_is_exact=exact_count_available,
            has_more=has_more,
            # None, not False, when nothing was asked: an ordinary search payload stays
            # exactly what it was before valid time existed.
            temporal_applied=True if temporal_requested else None,
        )
