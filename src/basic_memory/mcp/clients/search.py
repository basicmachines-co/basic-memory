"""Typed client for search API operations.

Encapsulates all /v2/projects/{project_id}/search/* endpoints.
"""

from typing import Any

from httpx import AsyncClient

import logfire

# call_* helpers live in basic_memory.mcp.tools.utils; importing that at module
# level executes the whole tools package (fastmcp + mcp SDK) during CLI startup,
# so each method defers the import to call time instead (#886).
from basic_memory.schemas.search import SearchResponse, SearchRetrievalMode

# The valid-time fields SearchQuery carries. Named here so the skew check below stays
# in step with the schema without importing the model's internals.
_TEMPORAL_QUERY_FIELDS = ("valid_at", "valid_overlaps", "time_kind")


class SearchClient:
    """Typed client for search operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/search/*
    - Response validation via Pydantic models
    - Consistent error handling through call_* utilities

    Usage:
        async with get_client() as http_client:
            client = SearchClient(http_client, project_id)
            results = await client.search(search_query.model_dump())
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the search client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/search"

    async def search(
        self,
        query: dict[str, Any],
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> SearchResponse:
        """Search across all content in the knowledge base.

        Args:
            query: Search query dict (from SearchQuery.model_dump())
            page: Page number (1-indexed)
            page_size: Results per page

        Returns:
            SearchResponse with results and pagination

        Raises:
            ToolError: If the request fails
            ValueError: If a requested valid-time filter was not applied by the server
        """
        from basic_memory.mcp.tools.utils import call_query

        with logfire.span(
            "mcp.client.search.search",
            client_name="search",
            operation="search",
            page=page,
            page_size=page_size,
        ):
            response = await call_query(
                self.http_client,
                f"{self._base_path}/",
                json=query,
                params={"page": page, "page_size": page_size},
                client_name="search",
                operation="search",
                path_template="/v2/projects/{project_id}/search/",
            )
        payload = response.json()

        # Trigger: an older API server omits the exactness field.
        # Why: the request mode still identifies whether that server used an exact count.
        # Outcome: legacy semantic responses stay unknown instead of becoming exact zeroes.
        if "total_is_exact" not in payload:
            retrieval_mode = query.get("retrieval_mode", SearchRetrievalMode.FTS)
            payload["total_is_exact"] = retrieval_mode == SearchRetrievalMode.FTS

        # Trigger: this request carried a valid-time filter but the response does not
        #   confirm the server ran it.
        # Why: SearchQuery ignores unknown fields, so a server predating SPEC-82 accepts
        #   the request and returns results that look filtered. A valid-time query
        #   excludes undated sources; unfiltered results include them, and the caller
        #   would have no way to tell.
        # Outcome: fail loudly instead of returning a wrong answer that reads as right.
        if any(query.get(field) for field in _TEMPORAL_QUERY_FIELDS) and (
            payload.get("temporal_applied") is not True
        ):
            raise ValueError(
                "The search API did not apply the requested valid-time filter "
                "(no temporal_applied confirmation in the response). The server is "
                "likely older than this client; upgrade it or drop valid_at / "
                "valid_overlaps / time_kind from the query."
            )

        return SearchResponse.model_validate(payload)
