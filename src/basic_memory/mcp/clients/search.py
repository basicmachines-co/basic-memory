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
        """
        from basic_memory.mcp.tools.utils import call_post

        with logfire.span(
            "mcp.client.search.search",
            client_name="search",
            operation="search",
            page=page,
            page_size=page_size,
        ):
            response = await call_post(
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

        return SearchResponse.model_validate(payload)
