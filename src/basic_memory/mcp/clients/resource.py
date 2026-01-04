"""Typed client for resource API operations.

Encapsulates all /v2/projects/{project_id}/resource/* endpoints.
"""

from typing import Optional

from httpx import AsyncClient, Response

from basic_memory.mcp.tools.utils import call_get


class ResourceClient:
    """Typed client for resource operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/resource/*
    - Consistent error handling through call_* utilities

    Note: This client returns raw Response objects for resources since they
    may be text, images, or other binary content that needs special handling.

    Usage:
        async with get_client() as http_client:
            client = ResourceClient(http_client, project_id)
            response = await client.read(entity_id)
            text = response.text
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the resource client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/resource"

    async def read(
        self,
        entity_id: str,
        *,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Response:
        """Read a resource by entity ID.

        Args:
            entity_id: Entity external_id (UUID)
            page: Optional page number for paginated content
            page_size: Optional page size for paginated content

        Returns:
            Raw HTTP Response (caller handles text/binary content)

        Raises:
            ToolError: If the resource is not found or request fails
        """
        params: dict = {}
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size

        return await call_get(
            self.http_client,
            f"{self._base_path}/{entity_id}",
            params=params if params else None,
        )
