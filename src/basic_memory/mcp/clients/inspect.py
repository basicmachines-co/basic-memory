"""Typed client for read-only retrieval inspection endpoints."""

from httpx import AsyncClient

import logfire
from basic_memory.schemas.inspect import InspectChunksRequest, InspectChunksResponse


class InspectClient:
    """Typed client for project-scoped retrieval inspection."""

    def __init__(self, http_client: AsyncClient, project_id: str):
        self.http_client = http_client
        self._base_path = f"/v2/projects/{project_id}/inspect"

    async def inspect_chunks(self, identifier: str) -> InspectChunksResponse:
        """Inspect one note's search rows and vector chunk manifest."""
        from basic_memory.mcp.tools.utils import call_post

        request = InspectChunksRequest(identifier=identifier)
        with logfire.span(
            "mcp.client.inspect.chunks",
            client_name="inspect",
            operation="chunks",
        ):
            response = await call_post(
                self.http_client,
                f"{self._base_path}/chunks",
                json=request.model_dump(mode="json"),
                client_name="inspect",
                operation="chunks",
                path_template="/v2/projects/{project_id}/inspect/chunks",
            )
        return InspectChunksResponse.model_validate(response.json())
