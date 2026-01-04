"""Typed client for memory/context API operations.

Encapsulates all /v2/projects/{project_id}/memory/* endpoints.
"""

from typing import Optional

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.memory import GraphContext


class MemoryClient:
    """Typed client for memory context operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/memory/*
    - Response validation via Pydantic models
    - Consistent error handling through call_* utilities

    Usage:
        async with get_client() as http_client:
            client = MemoryClient(http_client, project_id)
            context = await client.build_context("memory://specs/search")
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the memory client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/memory"

    async def build_context(
        self,
        path: str,
        *,
        depth: int = 1,
        timeframe: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        max_related: int = 10,
    ) -> GraphContext:
        """Build context from a memory path.

        Args:
            path: The path to build context for (without memory:// prefix)
            depth: How deep to traverse relations
            timeframe: Time filter (e.g., "7d", "1 week")
            page: Page number (1-indexed)
            page_size: Results per page
            max_related: Maximum related items per result

        Returns:
            GraphContext with hierarchical results

        Raises:
            ToolError: If the request fails
        """
        params: dict = {
            "depth": depth,
            "page": page,
            "page_size": page_size,
            "max_related": max_related,
        }
        if timeframe:
            params["timeframe"] = timeframe

        response = await call_get(
            self.http_client,
            f"{self._base_path}/{path}",
            params=params,
        )
        return GraphContext.model_validate(response.json())

    async def recent(
        self,
        *,
        timeframe: str = "7d",
        depth: int = 1,
        types: Optional[list[str]] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> GraphContext:
        """Get recent activity.

        Args:
            timeframe: Time filter (e.g., "7d", "1 week", "2 days ago")
            depth: How deep to traverse relations
            types: Filter by item types
            page: Page number (1-indexed)
            page_size: Results per page

        Returns:
            GraphContext with recent activity

        Raises:
            ToolError: If the request fails
        """
        params: dict = {
            "timeframe": timeframe,
            "depth": depth,
            "page": page,
            "page_size": page_size,
        }
        if types:
            # Join types as comma-separated string if provided
            params["type"] = ",".join(types) if isinstance(types, list) else types

        response = await call_get(
            self.http_client,
            f"{self._base_path}/recent",
            params=params,
        )
        return GraphContext.model_validate(response.json())
