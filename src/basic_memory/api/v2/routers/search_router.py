"""V2 router for search operations.

This router uses external_id UUIDs for stable, API-friendly routing.
V1 uses string-based project names which are less efficient and less stable.
"""

import asyncio
import json
from contextlib import nullcontext
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response

import logfire
from basic_memory.api.v2.utils import to_search_results
from basic_memory.deps import (
    EntityServiceV2ExternalDep,
    ProjectExternalIdPathDep,
    ReadCacheDep,
    SearchReindexSchedulerDep,
    SearchServiceV2ExternalDep,
    create_model_read_cache,
)
from basic_memory.read_cache import (
    ModelReadCache,
    ReadCacheKey,
    ReadCacheOperation,
    ReadCacheScope,
    read_cache_request_digest,
)
from basic_memory.read_cache.policy import SEARCH_READ_CACHE_TTL_SECONDS
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.schemas.search import SearchQuery, SearchResponse, SearchRetrievalMode

# App registration mounts this router at /v2/projects/{project_id}.
router = APIRouter(tags=["search"])


def get_search_read_cache(
    read_cache: ReadCacheDep,
) -> ModelReadCache[SearchResponse] | None:
    """Bind search responses to the optional cache backend and short query TTL."""
    return create_model_read_cache(
        read_cache,
        SearchResponse,
        ttl_seconds=SEARCH_READ_CACHE_TTL_SECONDS,
    )


SearchReadCacheDep = Annotated[
    ModelReadCache[SearchResponse] | None,
    Depends(get_search_read_cache),
]


def _search_request_digest(query: SearchQuery, *, page: int, page_size: int) -> str:
    """Return one structural identity for POST and QUERY search requests."""
    canonical_request = json.dumps(
        {
            "query": query.model_dump(mode="json"),
            "page": page,
            "page_size": page_size,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return read_cache_request_digest(canonical_request)


@router.api_route(
    "/search/",
    methods=["QUERY"],
    response_model=SearchResponse,
    include_in_schema=False,
)
@router.post("/search/", response_model=SearchResponse)
async def search(
    query: SearchQuery,
    search_service: SearchServiceV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    read_cache: SearchReadCacheDep,
    response: Response,
    project_id: str = Path(..., description="Project external UUID"),
    page: int = 1,
    page_size: int = 10,
) -> SearchResponse:
    """Search across all knowledge and documents in a project.

    V2 uses external_id UUIDs for stable API references.

    Args:
        project_id: Project external UUID from URL path
        query: Search query parameters (text, filters, etc.)
        search_service: Search service scoped to project
        entity_service: Entity service scoped to project
        page: Page number for pagination
        page_size: Number of results per page

    Returns:
        SearchResponse with paginated search results
    """
    response.headers["Accept-Query"] = "application/json"
    with logfire.span(
        "api.request.search",
        entrypoint="api",
        domain="search",
        action="search",
        page=page,
        page_size=page_size,
        retrieval_mode=query.retrieval_mode.value,
        has_query=bool(
            (query.text and query.text.strip())
            or query.title
            or query.permalink
            or query.permalink_match
        ),
        has_filters=bool(
            query.note_types
            or query.entity_types
            or query.categories
            or query.metadata_filters
            or query.file_path_prefix
        ),
    ):
        cache_key = ReadCacheKey(
            project_id=project_id,
            operation=ReadCacheOperation.search,
            request_digest=_search_request_digest(query, page=page, page_size=page_size),
        )
        cache_scope = (
            read_cache.read(key=cache_key)
            if read_cache is not None
            else nullcontext(ReadCacheScope[SearchResponse]())
        )
        async with cache_scope as cached:
            if cached.value is not None:
                return cached.value

            offset = (page - 1) * page_size
            exact_count_available = query.retrieval_mode == SearchRetrievalMode.FTS
            try:
                with logfire.span(
                    "api.search.search.execute_query",
                    domain="search",
                    action="search",
                    phase="execute_query",
                    page=page,
                    page_size=page_size,
                ):
                    if exact_count_available:
                        results, total = await asyncio.gather(
                            search_service.search(query, limit=page_size, offset=offset),
                            search_service.count(query),
                        )
                    else:
                        results = await search_service.search(
                            query, limit=page_size + 1, offset=offset
                        )
                        total = 0
            except SemanticSearchDisabledError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except SemanticDependenciesMissingError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RerankTransientError as exc:
                # Returning raw retrieval order would make pagination inconsistent with
                # earlier reranked pages. Preserve ordering semantics and make the outage
                # explicitly retryable instead.
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except RerankProviderContractError as exc:
                # Upstream reranker returned a malformed response — an upstream fault, not a
                # client error and not a transient outage (those map to a retryable 503).
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            with logfire.span(
                "api.search.search.paginate_results",
                domain="search",
                action="search",
                phase="paginate_results",
                result_count=len(results),
            ):
                if exact_count_available:
                    has_more = offset + len(results) < total
                else:
                    # Trigger: semantic modes would need another vector/hybrid retrieval to count.
                    # Why: search requests should not pay for a second semantic pass.
                    # Outcome: preserve probe pagination, leave total at 0, and mark it unknown.
                    has_more = len(results) > page_size
                    if has_more:
                        results = results[:page_size]

            with logfire.span(
                "api.search.search.hydrate_results",
                domain="search",
                action="search",
                phase="hydrate_results",
                result_count=len(results),
            ):
                search_results = await to_search_results(entity_service, results)
            with logfire.span(
                "api.search.search.build_response",
                domain="search",
                action="search",
                phase="build_response",
                result_count=len(search_results),
            ):
                result = SearchResponse(
                    results=search_results,
                    current_page=page,
                    page_size=page_size,
                    total=total,
                    total_is_exact=exact_count_available,
                    has_more=has_more,
                )
                cached.value = result
                return result


@router.post("/search/reindex")
async def reindex(
    search_reindex_scheduler: SearchReindexSchedulerDep,
    project_id: ProjectExternalIdPathDep,
):
    """Recreate and populate the search index for a project.

    This is a background operation that rebuilds the search index
    from scratch. Useful after bulk updates or if the index becomes
    corrupted.

    Args:
        project_id: Project external UUID from URL path
        search_reindex_scheduler: Search reindex scheduler for background work

    Returns:
        Status message indicating reindex has been initiated
    """
    search_reindex_scheduler.schedule_search_reindex(project_id=project_id)
    return {"status": "ok", "message": "Reindex initiated"}
