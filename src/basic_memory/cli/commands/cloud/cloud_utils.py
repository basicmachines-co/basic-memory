"""Shared utilities for cloud operations."""

from basic_memory.cli.commands.cloud.api_client import make_api_request
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import resolve_configured_workspace
from basic_memory.schemas.cloud import (
    CloudProjectList,
    CloudProjectCreateRequest,
    CloudProjectCreateResponse,
    ProjectVisibility,
)
from basic_memory.utils import generate_permalink


class CloudUtilsError(Exception):
    """Exception raised for cloud utility errors."""

    pass


def _workspace_headers(
    *,
    project_name: str | None = None,
    workspace: str | None = None,
) -> dict[str, str]:
    """Build optional workspace headers using the CLI config resolution chain."""
    resolved_workspace = resolve_configured_workspace(
        project_name=project_name,
        workspace=workspace,
    )
    if resolved_workspace is None:
        return {}
    return {"X-Workspace-ID": resolved_workspace}


async def fetch_cloud_projects(
    *,
    project_name: str | None = None,
    workspace: str | None = None,
    api_request=make_api_request,
) -> CloudProjectList:
    """Fetch list of projects from cloud API.

    Args:
        project_name: Optional project name for workspace resolution
        workspace: Cloud workspace tenant_id to list projects from

    Returns:
        CloudProjectList with projects from cloud
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await api_request(
            method="GET",
            url=f"{host_url}/proxy/v2/projects/",
            headers=_workspace_headers(project_name=project_name, workspace=workspace),
        )

        return CloudProjectList.model_validate(response.json())
    except Exception as e:
        raise CloudUtilsError(f"Failed to fetch cloud projects: {e}") from e


async def create_cloud_project(
    project_name: str,
    *,
    workspace: str | None = None,
    visibility: ProjectVisibility = "workspace",
    api_request=make_api_request,
) -> CloudProjectCreateResponse:
    """Create a new project on cloud.

    Args:
        project_name: Name of project to create
        workspace: Optional workspace override for tenant-scoped project creation
        visibility: Visibility for the created cloud project

    Returns:
        CloudProjectCreateResponse with project details from API
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        # Use generate_permalink to ensure consistent naming
        project_path = generate_permalink(project_name)

        project_data = CloudProjectCreateRequest(
            name=project_name,
            path=project_path,
            set_default=False,
            visibility=visibility,
        )

        response = await api_request(
            method="POST",
            url=f"{host_url}/proxy/v2/projects/",
            headers={
                "Content-Type": "application/json",
                **_workspace_headers(project_name=project_name, workspace=workspace),
            },
            json_data=project_data.model_dump(),
        )

        return CloudProjectCreateResponse.model_validate(response.json())
    except Exception as e:
        raise CloudUtilsError(f"Failed to create cloud project '{project_name}': {e}") from e


async def sync_project(project_name: str, force_full: bool = False) -> None:
    """Trigger sync for a specific project on cloud.

    Args:
        project_name: Name of project to sync
        force_full: ignored, kept for backwards compatibility
    """
    try:
        from basic_memory.cli.commands.command_utils import run_sync

        await run_sync(project=project_name)
    except Exception as e:
        raise CloudUtilsError(f"Failed to sync project '{project_name}': {e}") from e


async def project_exists(
    project_name: str,
    *,
    workspace: str | None = None,
    api_request=make_api_request,
) -> bool:
    """Check if a project exists on cloud.

    Args:
        project_name: Name of project to check
        workspace: Optional workspace override for tenant-scoped project lookup

    Returns:
        True if project exists, False otherwise

    Raises:
        CloudUtilsError: If the project list cannot be fetched from cloud
    """
    projects = await fetch_cloud_projects(
        project_name=project_name,
        workspace=workspace,
        api_request=api_request,
    )
    project_names = {p.name for p in projects.projects}
    return project_name in project_names
