"""Typed client for project API operations.

Encapsulates project-level endpoints.
"""

from typing import Any

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_get, call_post, call_delete
from basic_memory.schemas.project_info import ProjectList, ProjectStatusResponse


class ProjectClient:
    """Typed client for project management operations.

    Centralizes:
    - API path construction for project endpoints
    - Response validation via Pydantic models
    - Consistent error handling through call_* utilities

    Note: This client does not require a project_id since it operates
    across projects.

    Usage:
        async with get_client() as http_client:
            client = ProjectClient(http_client)
            projects = await client.list_projects()
    """

    def __init__(self, http_client: AsyncClient):
        """Initialize the project client.

        Args:
            http_client: HTTPX AsyncClient for making requests
        """
        self.http_client = http_client

    async def list_projects(self) -> ProjectList:
        """List all available projects.

        Returns:
            ProjectList with all projects and default project name

        Raises:
            ToolError: If the request fails
        """
        response = await call_get(
            self.http_client,
            "/v2/projects/",
        )
        return ProjectList.model_validate(response.json())

    async def create_project(self, project_data: dict[str, Any]) -> ProjectStatusResponse:
        """Create a new project.

        Args:
            project_data: Project creation data (name, path, set_default)

        Returns:
            ProjectStatusResponse with creation result

        Raises:
            ToolError: If the request fails
        """
        response = await call_post(
            self.http_client,
            "/v2/projects/",
            json=project_data,
        )
        return ProjectStatusResponse.model_validate(response.json())

    async def delete_project(self, project_external_id: str) -> ProjectStatusResponse:
        """Delete a project by its external ID.

        Args:
            project_external_id: Project external ID (UUID)

        Returns:
            ProjectStatusResponse with deletion result

        Raises:
            ToolError: If the request fails
        """
        response = await call_delete(
            self.http_client,
            f"/v2/projects/{project_external_id}",
        )
        return ProjectStatusResponse.model_validate(response.json())
