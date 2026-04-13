"""Typed client for knowledge/entity API operations.

Encapsulates all /v2/projects/{project_id}/knowledge/* endpoints.
"""

from typing import Any

from httpx import AsyncClient

from basic_memory import telemetry
from basic_memory.mcp.tools.utils import call_get, call_post, call_put, call_patch, call_delete
from basic_memory.schemas.response import (
    EntityResponse,
    DeleteEntitiesResponse,
    DirectoryMoveResult,
    DirectoryDeleteResult,
)


class KnowledgeClient:
    """Typed client for knowledge graph entity operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/knowledge/*
    - Response validation via Pydantic models
    - Consistent error handling through call_* utilities

    Usage:
        async with get_client() as http_client:
            client = KnowledgeClient(http_client, project_id)
            entity = await client.create_entity(entity_data)
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the knowledge client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/knowledge"

    # --- Entity CRUD Operations ---

    async def create_entity(self, entity_data: dict[str, Any]) -> EntityResponse:
        """Create a new entity.

        Args:
            entity_data: Entity data including title, content, folder, etc.

        Returns:
            EntityResponse with created entity details

        Raises:
            ToolError: If the request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.create_entity",
            client_name="knowledge",
            operation="create_entity",
        ):
            response = await call_post(
                self.http_client,
                f"{self._base_path}/entities",
                json=entity_data,
                client_name="knowledge",
                operation="create_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities",
            )
        return EntityResponse.model_validate(response.json())

    async def update_entity(
        self,
        entity_id: str,
        entity_data: dict[str, Any],
    ) -> EntityResponse:
        """Update an existing entity (full replacement).

        Args:
            entity_id: Entity external_id (UUID)
            entity_data: Complete entity data for replacement

        Returns:
            EntityResponse with updated entity details

        Raises:
            ToolError: If the request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.update_entity",
            client_name="knowledge",
            operation="update_entity",
        ):
            response = await call_put(
                self.http_client,
                f"{self._base_path}/entities/{entity_id}",
                json=entity_data,
                client_name="knowledge",
                operation="update_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}",
            )
        return EntityResponse.model_validate(response.json())

    async def get_entity(self, entity_id: str) -> EntityResponse:
        """Get an entity by ID.

        Args:
            entity_id: Entity external_id (UUID)

        Returns:
            EntityResponse with entity details

        Raises:
            ToolError: If the entity is not found or request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.get_entity",
            client_name="knowledge",
            operation="get_entity",
        ):
            response = await call_get(
                self.http_client,
                f"{self._base_path}/entities/{entity_id}",
                client_name="knowledge",
                operation="get_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}",
            )
        return EntityResponse.model_validate(response.json())

    async def patch_entity(
        self,
        entity_id: str,
        patch_data: dict[str, Any],
    ) -> EntityResponse:
        """Partially update an entity.

        Args:
            entity_id: Entity external_id (UUID)
            patch_data: Partial entity data to update

        Returns:
            EntityResponse with updated entity details

        Raises:
            ToolError: If the request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.patch_entity",
            client_name="knowledge",
            operation="patch_entity",
        ):
            response = await call_patch(
                self.http_client,
                f"{self._base_path}/entities/{entity_id}",
                json=patch_data,
                client_name="knowledge",
                operation="patch_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}",
            )
        return EntityResponse.model_validate(response.json())

    async def delete_entity(self, entity_id: str) -> DeleteEntitiesResponse:
        """Delete an entity.

        Args:
            entity_id: Entity external_id (UUID)

        Returns:
            DeleteEntitiesResponse confirming deletion

        Raises:
            ToolError: If the entity is not found or request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.delete_entity",
            client_name="knowledge",
            operation="delete_entity",
        ):
            response = await call_delete(
                self.http_client,
                f"{self._base_path}/entities/{entity_id}",
                client_name="knowledge",
                operation="delete_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}",
            )
        return DeleteEntitiesResponse.model_validate(response.json())

    async def move_entity(self, entity_id: str, destination_path: str) -> EntityResponse:
        """Move an entity to a new location.

        Args:
            entity_id: Entity external_id (UUID)
            destination_path: New file path for the entity

        Returns:
            EntityResponse with updated entity details

        Raises:
            ToolError: If the request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.move_entity",
            client_name="knowledge",
            operation="move_entity",
        ):
            response = await call_put(
                self.http_client,
                f"{self._base_path}/entities/{entity_id}/move",
                json={"destination_path": destination_path},
                client_name="knowledge",
                operation="move_entity",
                path_template="/v2/projects/{project_id}/knowledge/entities/{entity_id}/move",
            )
        return EntityResponse.model_validate(response.json())

    async def move_directory(
        self, source_directory: str, destination_directory: str
    ) -> DirectoryMoveResult:
        """Move all entities in a directory to a new location.

        Args:
            source_directory: Source directory path (relative to project root)
            destination_directory: Destination directory path (relative to project root)

        Returns:
            DirectoryMoveResult with counts and details of moved files

        Raises:
            ToolError: If the request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.move_directory",
            client_name="knowledge",
            operation="move_directory",
        ):
            response = await call_post(
                self.http_client,
                f"{self._base_path}/move-directory",
                json={
                    "source_directory": source_directory,
                    "destination_directory": destination_directory,
                },
                client_name="knowledge",
                operation="move_directory",
                path_template="/v2/projects/{project_id}/knowledge/move-directory",
            )
        return DirectoryMoveResult.model_validate(response.json())

    async def delete_directory(self, directory: str) -> DirectoryDeleteResult:
        """Delete all entities in a directory.

        Args:
            directory: Directory path to delete (relative to project root)

        Returns:
            DirectoryDeleteResult with counts and details of deleted files

        Raises:
            ToolError: If the request fails
        """
        with telemetry.scope(
            "mcp.client.knowledge.delete_directory",
            client_name="knowledge",
            operation="delete_directory",
        ):
            response = await call_post(
                self.http_client,
                f"{self._base_path}/delete-directory",
                json={"directory": directory},
                client_name="knowledge",
                operation="delete_directory",
                path_template="/v2/projects/{project_id}/knowledge/delete-directory",
            )
        return DirectoryDeleteResult.model_validate(response.json())

    # --- Resolution ---

    async def resolve_entity(self, identifier: str, *, strict: bool = False) -> str:
        """Resolve a string identifier to an entity external_id.

        Args:
            identifier: The identifier to resolve (permalink, title, or path)
            strict: If True, require exact matching (no fuzzy fallback)

        Returns:
            The resolved entity external_id (UUID)

        Raises:
            ToolError: If the identifier cannot be resolved
        """
        with telemetry.scope(
            "mcp.client.knowledge.resolve_entity",
            client_name="knowledge",
            operation="resolve_entity",
        ):
            response = await call_post(
                self.http_client,
                f"{self._base_path}/resolve",
                json={"identifier": identifier, "strict": strict},
                client_name="knowledge",
                operation="resolve_entity",
                path_template="/v2/projects/{project_id}/knowledge/resolve",
            )
        data = response.json()
        return data["external_id"]
