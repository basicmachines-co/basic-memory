"""Typed client for project API operations.

Encapsulates project-level endpoints.
"""

from typing import Any

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import (
    call_delete,
    call_get,
    call_patch,
    call_post,
    call_put,
)
from basic_memory.schemas import ProjectInfoResponse, SyncReportResponse
from basic_memory.schemas.project_info import ProjectList, ProjectStatusResponse
from basic_memory.schemas.v2 import ProjectResolveResponse


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

    async def delete_project(
        self, project_external_id: str, delete_notes: bool = False
    ) -> ProjectStatusResponse:
        """Delete a project by its external ID.

        Args:
            project_external_id: Project external ID (UUID)
            delete_notes: If True, also delete project files from disk

        Returns:
            ProjectStatusResponse with deletion result

        Raises:
            ToolError: If the request fails
        """
        url = f"/v2/projects/{project_external_id}"
        if delete_notes:
            url += "?delete_notes=true"
        response = await call_delete(
            self.http_client,
            url,
        )
        return ProjectStatusResponse.model_validate(response.json())

    async def resolve_project(self, identifier: str) -> ProjectResolveResponse:
        """Resolve a project name/permalink to its full project record.

        Args:
            identifier: Project name or permalink

        Returns:
            ProjectResolveResponse with project metadata

        Raises:
            ToolError: If the request fails
        """
        response = await call_post(
            self.http_client,
            "/v2/projects/resolve",
            json={"identifier": identifier},
        )
        return ProjectResolveResponse.model_validate(response.json())

    async def set_default(self, project_external_id: str) -> ProjectStatusResponse:
        """Set a project as the default.

        Args:
            project_external_id: Project external ID (UUID)

        Returns:
            ProjectStatusResponse with result

        Raises:
            ToolError: If the request fails
        """
        response = await call_put(
            self.http_client,
            f"/v2/projects/{project_external_id}/default",
        )
        return ProjectStatusResponse.model_validate(response.json())

    async def update_project(
        self, project_external_id: str, data: dict[str, Any]
    ) -> ProjectStatusResponse:
        """Update a project's configuration (e.g. path).

        Args:
            project_external_id: Project external ID (UUID)
            data: Fields to update

        Returns:
            ProjectStatusResponse with update result

        Raises:
            ToolError: If the request fails
        """
        response = await call_patch(
            self.http_client,
            f"/v2/projects/{project_external_id}",
            json=data,
        )
        return ProjectStatusResponse.model_validate(response.json())

    async def sync(
        self,
        project_external_id: str,
        force_full: bool = False,
        run_in_background: bool = True,
    ) -> dict[str, Any]:
        """Trigger a sync operation for a project.

        Args:
            project_external_id: Project external ID (UUID)
            force_full: If True, force a full scan bypassing watermark optimization
            run_in_background: If True, return immediately; if False, wait for completion

        Returns:
            Raw response dict — background mode returns {"message": ...},
            foreground mode returns a SyncReportResponse-shaped dict.

        Raises:
            ToolError: If the request fails
        """
        url = f"/v2/projects/{project_external_id}/sync"
        params = []
        if force_full:
            params.append("force_full=true")
        if not run_in_background:
            params.append("run_in_background=false")
        if params:
            url += "?" + "&".join(params)
        response = await call_post(self.http_client, url)
        return response.json()

    async def get_status(self, project_external_id: str) -> SyncReportResponse:
        """Get the sync status for a project.

        Args:
            project_external_id: Project external ID (UUID)

        Returns:
            SyncReportResponse describing pending changes

        Raises:
            ToolError: If the request fails
        """
        response = await call_post(
            self.http_client,
            f"/v2/projects/{project_external_id}/status",
        )
        return SyncReportResponse.model_validate(response.json())

    async def get_info(self, project_external_id: str) -> ProjectInfoResponse:
        """Get detailed project information and statistics.

        Args:
            project_external_id: Project external ID (UUID)

        Returns:
            ProjectInfoResponse with project details

        Raises:
            ToolError: If the request fails
        """
        response = await call_get(
            self.http_client,
            f"/v2/projects/{project_external_id}/info",
        )
        return ProjectInfoResponse.model_validate(response.json())
