"""Router for project management."""

import os
from fastapi import APIRouter, HTTPException, Path, Body, BackgroundTasks, Response, Query
from typing import Optional
from loguru import logger

from basic_memory.deps import (
    ProjectConfigDep,
    ProjectServiceDep,
    ProjectPathDep,
    SyncServiceDep,
)
from basic_memory.schemas import ProjectInfoResponse, SyncReportResponse
from basic_memory.schemas.project_info import (
    ProjectList,
    ProjectItem,
    ProjectInfoRequest,
    ProjectStatusResponse,
)
from basic_memory.utils import normalize_project_path

# Router for resources in a specific project
# The ProjectPathDep is used in the path as a prefix, so the request path is like /{project}/project/info
project_router = APIRouter(prefix="/project", tags=["project"])

# Router for managing project resources
project_resource_router = APIRouter(prefix="/projects", tags=["project_management"])


@project_router.get("/info", response_model=ProjectInfoResponse)
async def get_project_info(
    project_service: ProjectServiceDep,
    project: ProjectPathDep,
) -> ProjectInfoResponse:
    """Get comprehensive information about the specified Basic Memory project."""
    return await project_service.get_project_info(project)


@project_router.get("/item", response_model=ProjectItem)
async def get_project(
    project_service: ProjectServiceDep,
    project: ProjectPathDep,
) -> ProjectItem:
    """Get bassic info about the specified Basic Memory project."""
    found_project = await project_service.get_project(project)
    if not found_project:
        raise HTTPException(
            status_code=404, detail=f"Project: '{project}' does not exist"
        )  # pragma: no cover

    return ProjectItem(
        id=found_project.id,
        name=found_project.name,
        path=normalize_project_path(found_project.path),
        is_default=found_project.is_default or False,
    )


# Update a project
@project_router.patch("/{name}", response_model=ProjectStatusResponse)
async def update_project(
    project_service: ProjectServiceDep,
    name: str = Path(..., description="Name of the project to update"),
    path: Optional[str] = Body(None, description="New absolute path for the project"),
    is_active: Optional[bool] = Body(None, description="Status of the project (active/inactive)"),
) -> ProjectStatusResponse:
    """Update a project's information in configuration and database.

    Args:
        name: The name of the project to update
        path: Optional new absolute path for the project
        is_active: Optional status update for the project

    Returns:
        Response confirming the project was updated
    """
    try:
        # Validate that path is absolute if provided
        if path and not os.path.isabs(path):
            raise HTTPException(status_code=400, detail="Path must be absolute")

        # Get original project info for the response
        old_project = await project_service.get_project(name)
        if not old_project:
            raise HTTPException(
                status_code=400, detail=f"Project '{name}' not found in configuration"
            )

        old_project_info = ProjectItem(
            id=old_project.id,
            name=old_project.name,
            path=old_project.path,
            is_default=old_project.is_default or False,
        )

        if path:
            await project_service.move_project(name, path)
        elif is_active is not None:
            await project_service.update_project(name, is_active=is_active)

        # Get updated project info
        updated_project = await project_service.get_project(name)
        if not updated_project:
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found after update")

        return ProjectStatusResponse(
            message=f"Project '{name}' updated successfully",
            status="success",
            default=(name == project_service.default_project),
            old_project=old_project_info,
            new_project=ProjectItem(
                id=updated_project.id,
                name=updated_project.name,
                path=updated_project.path,
                is_default=updated_project.is_default or False,
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Sync project filesystem
@project_router.post("/sync")
async def sync_project(
    background_tasks: BackgroundTasks,
    sync_service: SyncServiceDep,
    project_config: ProjectConfigDep,
    force_full: bool = Query(
        False, description="Force full scan, bypassing watermark optimization"
    ),
    run_in_background: bool = Query(True, description="Run in background"),
):
    """Force project filesystem sync to database.

    Scans the project directory and updates the database with any new or modified files.

    Args:
        background_tasks: FastAPI background tasks
        sync_service: Sync service for this project
        project_config: Project configuration
        force_full: If True, force a full scan even if watermark exists
        run_in_background: If True, run sync in background and return immediately

    Returns:
        Response confirming sync was initiated (background) or SyncReportResponse (foreground)
    """
    if run_in_background:
        background_tasks.add_task(
            sync_service.sync, project_config.home, project_config.name, force_full=force_full
        )
        logger.info(
            f"Filesystem sync initiated for project: {project_config.name} (force_full={force_full})"
        )

        return {
            "status": "sync_started",
            "message": f"Filesystem sync initiated for project '{project_config.name}'",
        }
    else:
        report = await sync_service.sync(
            project_config.home, project_config.name, force_full=force_full
        )
        logger.info(
            f"Filesystem sync completed for project: {project_config.name} (force_full={force_full})"
        )
        return SyncReportResponse.from_sync_report(report)


@project_router.post("/status", response_model=SyncReportResponse)
async def project_sync_status(
    sync_service: SyncServiceDep,
    project_config: ProjectConfigDep,
) -> SyncReportResponse:
    """Scan directory for changes compared to database state.

    Args:
        sync_service: Sync service for this project
        project_config: Project configuration

    Returns:
        Scan report with details on files that need syncing
    """
    logger.info(f"Scanning filesystem for project: {project_config.name}")
    sync_report = await sync_service.scan(project_config.home)

    return SyncReportResponse.from_sync_report(sync_report)


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
            id=project.id,
            name=project.name,
            path=normalize_project_path(project.path),
            is_default=project.is_default or False,
        )
        for project in projects
    ]

    return ProjectList(
        projects=project_items,
        default_project=default_project,
    )


# Add a new project
@project_resource_router.post("/projects", response_model=ProjectStatusResponse, status_code=201)
async def add_project(
    response: Response,
    project_data: ProjectInfoRequest,
    project_service: ProjectServiceDep,
) -> ProjectStatusResponse:
    """Add a new project to configuration and database.

    Args:
        project_data: The project name and path, with option to set as default

    Returns:
        Response confirming the project was added
    """
    # Check if project already exists before attempting to add
    existing_project = await project_service.get_project(project_data.name)
    if existing_project:
        # Project exists - check if paths match for true idempotency
        # Normalize paths for comparison (resolve symlinks, etc.)
        from pathlib import Path

        requested_path = Path(project_data.path).resolve()
        existing_path = Path(existing_project.path).resolve()

        if requested_path == existing_path:
            # Same name, same path - return 200 OK (idempotent)
            response.status_code = 200
            return ProjectStatusResponse(  # pyright: ignore [reportCallIssue]
                message=f"Project '{project_data.name}' already exists",
                status="success",
                default=existing_project.is_default or False,
                new_project=ProjectItem(
                    id=existing_project.id,
                    name=existing_project.name,
                    path=existing_project.path,
                    is_default=existing_project.is_default or False,
                ),
            )
        else:
            # Same name, different path - this is an error
            raise HTTPException(
                status_code=400,
                detail=f"Project '{project_data.name}' already exists with different path. Existing: {existing_project.path}, Requested: {project_data.path}",
            )

    try:  # pragma: no cover
        # The service layer now handles cloud mode validation and path sanitization
        await project_service.add_project(
            project_data.name, project_data.path, set_default=project_data.set_default
        )

        # Fetch the newly created project to get its ID
        new_project = await project_service.get_project(project_data.name)
        if not new_project:
            raise HTTPException(status_code=500, detail="Failed to retrieve newly created project")

        return ProjectStatusResponse(  # pyright: ignore [reportCallIssue]
            message=f"Project '{project_data.name}' added successfully",
            status="success",
            default=project_data.set_default,
            new_project=ProjectItem(
                id=new_project.id,
                name=new_project.name,
                path=new_project.path,
                is_default=new_project.is_default or False,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Remove a project
@project_resource_router.delete("/{name}", response_model=ProjectStatusResponse)
async def remove_project(
    project_service: ProjectServiceDep,
    name: str = Path(..., description="Name of the project to remove"),
    delete_notes: bool = Query(
        False, description="If True, delete project directory from filesystem"
    ),
) -> ProjectStatusResponse:
    """Remove a project from configuration and database.

    Args:
        name: The name of the project to remove
        delete_notes: If True, delete the project directory from the filesystem

    Returns:
        Response confirming the project was removed
    """
    try:
        old_project = await project_service.get_project(name)
        if not old_project:  # pragma: no cover
            raise HTTPException(
                status_code=404, detail=f"Project: '{name}' does not exist"
            )  # pragma: no cover

        # Check if trying to delete the default project
        if name == project_service.default_project:
            available_projects = await project_service.list_projects()
            other_projects = [p.name for p in available_projects if p.name != name]
            detail = f"Cannot delete default project '{name}'. "
            if other_projects:
                detail += (
                    f"Set another project as default first. Available: {', '.join(other_projects)}"
                )
            else:
                detail += "This is the only project in your configuration."
            raise HTTPException(status_code=400, detail=detail)

        await project_service.remove_project(name, delete_notes=delete_notes)

        return ProjectStatusResponse(
            message=f"Project '{name}' removed successfully",
            status="success",
            default=False,
            old_project=ProjectItem(
                id=old_project.id,
                name=old_project.name,
                path=old_project.path,
                is_default=old_project.is_default or False,
            ),
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
            raise HTTPException(
                status_code=404, detail=f"Project: '{name}' does not exist"
            )  # pragma: no cover

        await project_service.set_default_project(name)

        return ProjectStatusResponse(
            message=f"Project '{name}' set as default successfully",
            status="success",
            default=True,
            old_project=ProjectItem(
                id=default_project.id,
                name=default_name,
                path=default_project.path,
                is_default=False,
            ),
            new_project=ProjectItem(
                id=new_default_project.id,
                name=name,
                path=new_default_project.path,
                is_default=True,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))


# Get the default project
@project_resource_router.get("/default", response_model=ProjectItem)
async def get_default_project(
    project_service: ProjectServiceDep,
) -> ProjectItem:
    """Get the default project.

    Returns:
        Response with project default information
    """
    # Get the old default project
    default_name = project_service.default_project
    default_project = await project_service.get_project(default_name)
    if not default_project:  # pragma: no cover
        raise HTTPException(  # pragma: no cover
            status_code=404, detail=f"Default Project: '{default_name}' does not exist"
        )

    return ProjectItem(
        id=default_project.id,
        name=default_project.name,
        path=default_project.path,
        is_default=True,
    )


# Synchronize projects between config and database
@project_resource_router.post("/config/sync", response_model=ProjectStatusResponse)
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
