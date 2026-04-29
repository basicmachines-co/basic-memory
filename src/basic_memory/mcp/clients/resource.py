"""Typed client for resource API operations.

Encapsulates all /v2/projects/{project_id}/resource/* endpoints.
"""

from httpx import AsyncClient, Response

import logfire
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

    async def read(self, entity_id: str) -> Response:
        """Read a resource by entity ID.

        Args:
            entity_id: Entity external_id (UUID)

        Returns:
            Raw HTTP Response (caller handles text/binary content)

        Raises:
            ToolError: If the resource is not found or request fails
        """
        with logfire.span(
            "mcp.client.resource.read",
            client_name="resource",
            operation="read",
        ):
            return await call_get(
                self.http_client,
                f"{self._base_path}/{entity_id}",
                client_name="resource",
                operation="read",
                path_template="/v2/projects/{project_id}/resource/{entity_id}",
            )
