"""Router for project management."""

from fastapi import APIRouter, HTTPException, Path, Body
from typing import Optional

from basic_memory.deps import ProjectServiceDep
from basic_memory.schemas import ProjectInfoResponse
from basic_memory.schemas.project_info import (
    ProjectList,
    ProjectItem,
    ProjectInfoRequest,
    ProjectStatusResponse,
)

# Router for resources in a specific project
project_router = APIRouter(prefix="/project", tags=["project"])

# Router for managing project resources
project_resource_router = APIRouter(prefix="/projects", tags=["project_management"])


@project_router.get("/info", response_model=ProjectInfoResponse)
async def get_project_info(
    project_service: ProjectServiceDep,
) -> ProjectInfoResponse:
    """Get comprehensive information about the current Basic Memory project."""
    return await project_service.get_project_info()


# Update a project
@project_router.patch("/{name}", response_model=ProjectStatusResponse)
async def update_project(
    project_service: ProjectServiceDep,
    project_name: str = Path(..., description="Name of the project to update"),
    path: Optional[str] = Body(None, description="New path for the project"),
    is_active: Optional[bool] = Body(None, description="Status of the project (active/inactive)"),
) -> ProjectStatusResponse:
    """Update a project's information in configuration and database.

    Args:
        project_name: The name of the project to update
        path: Optional new path for the project
        is_active: Optional status update for the project

    Returns:
        Response confirming the project was updated
    """
    try:  # pragma: no cover
        # Get original project info for the response
        old_project = ProjectItem(
            name=project_name,
            path=project_service.projects.get(project_name, ""),
        )

        await project_service.update_project(project_name, updated_path=path, is_active=is_active)

        # Get updated project info
        updated_path = path if path else project_service.projects.get(project_name, "")

        return ProjectStatusResponse(
            message=f"Project '{project_name}' updated successfully",
            status="success",
            default=(project_name == project_service.default_project),
            old_project=old_project,
            new_project=ProjectItem(name=project_name, path=updated_path),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# List all available projects
@project_resource_router.get("/projects", response_model=ProjectList)
async def list_projects(
    project_service: ProjectServiceDep,
) -> ProjectList:
    """List all configured projects.

    Returns:
        A list of all projects with metadata
    """
    projects = await project_service.list_projects()
    default_project = project_service.default_project

    project_items = [
        ProjectItem(
            name=project.name,
            path=project.path,
            is_default=project.is_default or False,
        )
        for project in projects
    ]

    return ProjectList(
        projects=project_items,
        default_project=default_project,
    )


# Add a new project
@project_resource_router.post("/projects", response_model=ProjectStatusResponse)
async def add_project(
    project_data: ProjectInfoRequest,
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
            new_project=ProjectItem(
                name=project_data.name, path=project_data.path, is_default=project_data.set_default
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Remove a project
@project_resource_router.delete("/{name}", response_model=ProjectStatusResponse)
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
    try:
        old_project = await project_service.get_project(name)
        if not old_project:  # pragma: no cover
            raise HTTPException(status_code=404, detail=f"Project: '{name}' does not exist")  # pragma: no cover

        await project_service.remove_project(name)

        return ProjectStatusResponse(
            message=f"Project '{name}' removed successfully",
            status="success",
            default=False,
            old_project=ProjectItem(name=old_project.name, path=old_project.path),
            new_project=None,
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Set a project as default
@project_resource_router.put("/{name}/default", response_model=ProjectStatusResponse)
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
    try:
        # Get the old default project
        default_name = project_service.default_project
        default_project = await project_service.get_project(default_name)
        if not default_project:  # pragma: no cover
            raise HTTPException(  # pragma: no cover
                status_code=404, detail=f"Default Project: '{default_name}' does not exist"
            )

        # get the new project
        new_default_project = await project_service.get_project(name)
        if not new_default_project:  # pragma: no cover
            raise HTTPException(status_code=404, detail=f"Project: '{name}' does not exist")  # pragma: no cover

        await project_service.set_default_project(name)

        return ProjectStatusResponse(
            message=f"Project '{name}' set as default successfully",
            status="success",
            default=True,
            old_project=ProjectItem(name=default_name, path=default_project.path),
            new_project=ProjectItem(
                name=name,
                path=new_default_project.path,
                is_default=True,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Synchronize projects between config and database
@project_resource_router.post("/sync", response_model=ProjectStatusResponse)
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
