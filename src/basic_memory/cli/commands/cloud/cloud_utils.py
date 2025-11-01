"""Shared utilities for cloud operations."""

from basic_memory.cli.commands.cloud.api_client import make_api_request
from basic_memory.config import ConfigManager
from basic_memory.schemas.cloud import (
    CloudProjectList,
    CloudProjectCreateRequest,
    CloudProjectCreateResponse,
)
from basic_memory.utils import generate_permalink


class CloudUtilsError(Exception):
    """Exception raised for cloud utility errors."""

    pass


async def fetch_cloud_projects() -> CloudProjectList:
    """Fetch list of projects from cloud API.

    Returns:
        CloudProjectList with projects from cloud
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(method="GET", url=f"{host_url}/proxy/projects/projects")

        return CloudProjectList.model_validate(response.json())
    except Exception as e:
        raise CloudUtilsError(f"Failed to fetch cloud projects: {e}") from e


async def create_cloud_project(project_name: str) -> CloudProjectCreateResponse:
    """Create a new project on cloud.

    Args:
        project_name: Name of project to create

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
        )

        response = await make_api_request(
            method="POST",
            url=f"{host_url}/proxy/projects/projects",
            headers={"Content-Type": "application/json"},
            json_data=project_data.model_dump(),
        )

        return CloudProjectCreateResponse.model_validate(response.json())
    except Exception as e:
        raise CloudUtilsError(f"Failed to create cloud project '{project_name}': {e}") from e


async def sync_project(project_name: str, force_full: bool = False) -> None:
    """Trigger sync for a specific project on cloud.

    Args:
        project_name: Name of project to sync
        force_full: If True, force a full scan bypassing watermark optimization
    """
    try:
        from basic_memory.cli.commands.command_utils import run_sync

        await run_sync(project=project_name, force_full=force_full)
    except Exception as e:
        raise CloudUtilsError(f"Failed to sync project '{project_name}': {e}") from e


async def project_exists(project_name: str) -> bool:
    """Check if a project exists on cloud.

    Args:
        project_name: Name of project to check

    Returns:
        True if project exists, False otherwise
    """
    try:
        projects = await fetch_cloud_projects()
        project_names = {p.name for p in projects.projects}
        return project_name in project_names
    except Exception:
        return False
