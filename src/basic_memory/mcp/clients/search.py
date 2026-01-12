"""Typed client for search API operations.

Encapsulates all /v2/projects/{project_id}/search/* endpoints.
"""

from typing import Any

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.search import SearchResponse


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
        response = await call_post(
            self.http_client,
            f"{self._base_path}/",
            json=query,
            params={"page": page, "page_size": page_size},
        )
        return SearchResponse.model_validate(response.json())
