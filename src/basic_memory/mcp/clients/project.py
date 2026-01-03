"""Typed client for project API operations.

Encapsulates project-level endpoints.
"""

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.project_info import ProjectList


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
            "/projects/projects",
        )
        return ProjectList.model_validate(response.json())
