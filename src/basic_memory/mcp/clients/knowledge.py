"""Typed client for knowledge/entity API operations.

Encapsulates all /v2/projects/{project_id}/knowledge/* endpoints.
"""

from typing import Any

from httpx import AsyncClient

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

    async def create_entity(
        self, entity_data: dict[str, Any], *, fast: bool | None = None
    ) -> EntityResponse:
        """Create a new entity.

        Args:
            entity_data: Entity data including title, content, folder, etc.

        Returns:
            EntityResponse with created entity details

        Raises:
            ToolError: If the request fails
        """
        params = {"fast": fast} if fast is not None else None
        response = await call_post(
            self.http_client,
            f"{self._base_path}/entities",
            json=entity_data,
            params=params,
        )
        return EntityResponse.model_validate(response.json())

    async def update_entity(
        self,
        entity_id: str,
        entity_data: dict[str, Any],
        *,
        fast: bool | None = None,
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
        params = {"fast": fast} if fast is not None else None
        response = await call_put(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
            json=entity_data,
            params=params,
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
        response = await call_get(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
        )
        return EntityResponse.model_validate(response.json())

    async def patch_entity(
        self,
        entity_id: str,
        patch_data: dict[str, Any],
        *,
        fast: bool | None = None,
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
        params = {"fast": fast} if fast is not None else None
        response = await call_patch(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
            json=patch_data,
            params=params,
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
        response = await call_delete(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}",
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
        response = await call_put(
            self.http_client,
            f"{self._base_path}/entities/{entity_id}/move",
            json={"destination_path": destination_path},
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
        response = await call_post(
            self.http_client,
            f"{self._base_path}/move-directory",
            json={
                "source_directory": source_directory,
                "destination_directory": destination_directory,
            },
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
        response = await call_post(
            self.http_client,
            f"{self._base_path}/delete-directory",
            json={"directory": directory},
        )
        return DirectoryDeleteResult.model_validate(response.json())

    # --- Resolution ---

    async def resolve_entity(self, identifier: str) -> str:
        """Resolve a string identifier to an entity external_id.

        Args:
            identifier: The identifier to resolve (permalink, title, or path)

        Returns:
            The resolved entity external_id (UUID)

        Raises:
            ToolError: If the identifier cannot be resolved
        """
        response = await call_post(
            self.http_client,
            f"{self._base_path}/resolve",
            json={"identifier": identifier},
        )
        data = response.json()
        return data["external_id"]
