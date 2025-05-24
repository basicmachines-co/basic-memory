"""Router for project management."""

from fastapi import APIRouter, HTTPException, Path, Body
from typing import Optional

from basic_memory.deps import ProjectServiceDep
from basic_memory.schemas import ProjectInfoResponse
from basic_memory.schemas.project_info import (
    ProjectList,
    ProjectItem,
    ProjectSwitchRequest,
    ProjectStatusResponse,
    ProjectWatchStatus,
)

# Define the router - we'll combine stats and project operations
router = APIRouter(prefix="/project", tags=["project"])


# Get project information (moved from project_info_router.py)
@router.get("/info", response_model=ProjectInfoResponse)
async def get_project_info(
    project_service: ProjectServiceDep,
) -> ProjectInfoResponse:
    """Get comprehensive information about the current Basic Memory project."""
    return await project_service.get_project_info()


# List all available projects
@router.get("/projects", response_model=ProjectList)
async def list_projects(
    project_service: ProjectServiceDep,
) -> ProjectList:
    """List all configured projects.

    Returns:
        A list of all projects with metadata
    """
    projects_dict = project_service.projects
    default_project = project_service.default_project
    current_project = project_service.current_project

    project_items = []
    for name, path in projects_dict.items():
        project_items.append(
            ProjectItem(
                name=name,
                path=path,
                is_default=(name == default_project),
                is_current=(name == current_project),
            )
        )

    return ProjectList(
        projects=project_items,
        default_project=default_project,
        current_project=current_project,
    )


# Add a new project
@router.post("/projects", response_model=ProjectStatusResponse)
async def add_project(
    project_data: ProjectSwitchRequest,
    project_service: ProjectServiceDep,
) -> ProjectStatusResponse:
    """Add a new project to configuration and database.

    Args:
        project_data: The project name and path, with option to set as default

    Returns:
        Response confirming the project was added
    """
    try:  # pragma: no cover
        await project_service.add_project(project_data.name, project_data.path)

        if project_data.set_default:  # pragma: no cover
            await project_service.set_default_project(project_data.name)

        return ProjectStatusResponse(  # pyright: ignore [reportCallIssue]
            message=f"Project '{project_data.name}' added successfully",
            status="success",
            default=project_data.set_default,
            new_project=ProjectWatchStatus(
                name=project_data.name,
                path=project_data.path,
                watch_status=None,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Remove a project
@router.delete("/projects/{name}", response_model=ProjectStatusResponse)
async def remove_project(
    project_service: ProjectServiceDep,
    name: str = Path(..., description="Name of the project to remove"),
) -> ProjectStatusResponse:
    """Remove a project from configuration and database.

    Args:
        name: The name of the project to remove

    Returns:
        Response confirming the project was removed
    """
    try:  # pragma: no cover
        # Get project info before removal for the response
        old_project = ProjectWatchStatus(
            name=name,
            path=project_service.projects.get(name, ""),
            watch_status=None,
        )

        await project_service.remove_project(name)

        return ProjectStatusResponse(  # pyright: ignore [reportCallIssue]
            message=f"Project '{name}' removed successfully",
            status="success",
            default=False,
            old_project=old_project,
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Set a project as default
@router.put("/projects/{name}/default", response_model=ProjectStatusResponse)
async def set_default_project(
    project_service: ProjectServiceDep,
    name: str = Path(..., description="Name of the project to set as default"),
) -> ProjectStatusResponse:
    """Set a project as the default project.

    Args:
        name: The name of the project to set as default

    Returns:
        Response confirming the project was set as default
    """
    try:  # pragma: no cover
        # Get the old default project
        old_default = project_service.default_project
        old_project = None
        if old_default != name:
            old_project = ProjectWatchStatus(
                name=old_default,
                path=project_service.projects.get(old_default, ""),
                watch_status=None,
            )

        await project_service.set_default_project(name)

        return ProjectStatusResponse(
            message=f"Project '{name}' set as default successfully",
            status="success",
            default=True,
            old_project=old_project,
            new_project=ProjectWatchStatus(
                name=name,
                path=project_service.projects.get(name, ""),
                watch_status=None,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Update a project
@router.patch("/projects/{name}", response_model=ProjectStatusResponse)
async def update_project(
    project_service: ProjectServiceDep,
    name: str = Path(..., description="Name of the project to update"),
    path: Optional[str] = Body(None, description="New path for the project"),
    is_active: Optional[bool] = Body(None, description="Status of the project (active/inactive)"),
) -> ProjectStatusResponse:
    """Update a project's information in configuration and database.

    Args:
        name: The name of the project to update
        path: Optional new path for the project
        is_active: Optional status update for the project

    Returns:
        Response confirming the project was updated
    """
    try:  # pragma: no cover
        # Get original project info for the response
        old_project = ProjectWatchStatus(
            name=name,
            path=project_service.projects.get(name, ""),
            watch_status=None,
        )

        await project_service.update_project(name, updated_path=path, is_active=is_active)

        # Get updated project info
        updated_path = path if path else project_service.projects.get(name, "")

        return ProjectStatusResponse(
            message=f"Project '{name}' updated successfully",
            status="success",
            default=(name == project_service.default_project),
            old_project=old_project,
            new_project=ProjectWatchStatus(name=name, path=updated_path, watch_status=None),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Synchronize projects between config and database
@router.post("/sync", response_model=ProjectStatusResponse)
async def synchronize_projects(
    project_service: ProjectServiceDep,
) -> ProjectStatusResponse:
    """Synchronize projects between configuration file and database.

    Ensures that all projects in the configuration file exist in the database
    and vice versa.

    Returns:
        Response confirming synchronization was completed
    """
    try:  # pragma: no cover
        await project_service.synchronize_projects()

        return ProjectStatusResponse(  # pyright: ignore [reportCallIssue]
            message="Projects synchronized successfully between configuration and database",
            status="success",
            default=False,
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))
