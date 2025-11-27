"""V2 Project Router - ID-based project management operations.

This router provides ID-based CRUD operations for projects, replacing the
name-based identifiers used in v1 with direct integer ID lookups.

Key improvements:
- Direct database lookups via integer primary keys
- Stable references that don't change with project renames
- Better performance through indexed queries
- Consistent with v2 entity operations
"""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Body, Query
from loguru import logger

from basic_memory.deps import (
    ProjectServiceDep,
    ProjectRepositoryDep,
    ProjectIdPathDep,
)
from basic_memory.schemas.project_info import (
    ProjectItem,
    ProjectStatusResponse,
)
from basic_memory.utils import normalize_project_path

router = APIRouter(prefix="/projects", tags=["project_management-v2"])


@router.get("/{project_id}", response_model=ProjectItem)
async def get_project_by_id(
    project_id: ProjectIdPathDep,
    project_repository: ProjectRepositoryDep,
) -> ProjectItem:
    """Get project by its numeric ID.

    This is the primary project retrieval method in v2, using direct database
    lookups for maximum performance.

    Args:
        project_id: Numeric project ID

    Returns:
        Project information

    Raises:
        HTTPException: 404 if project not found

    Example:
        GET /v2/projects/3
    """
    logger.info(f"API v2 request: get_project_by_id for project_id={project_id}")

    project = await project_repository.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project with ID {project_id} not found")

    return ProjectItem(
        id=project.id,
        name=project.name,
        path=normalize_project_path(project.path),
        is_default=project.is_default or False,
    )


@router.patch("/{project_id}", response_model=ProjectStatusResponse)
async def update_project_by_id(
    project_id: ProjectIdPathDep,
    project_service: ProjectServiceDep,
    project_repository: ProjectRepositoryDep,
    path: Optional[str] = Body(None, description="New absolute path for the project"),
    is_active: Optional[bool] = Body(None, description="Status of the project (active/inactive)"),
) -> ProjectStatusResponse:
    """Update a project's information by ID.

    Args:
        project_id: Numeric project ID
        path: Optional new absolute path for the project
        is_active: Optional status update for the project

    Returns:
        Response confirming the project was updated

    Raises:
        HTTPException: 400 if validation fails, 404 if project not found

    Example:
        PATCH /v2/projects/3
        {"path": "/new/path"}
    """
    logger.info(f"API v2 request: update_project_by_id for project_id={project_id}")

    try:
        # Validate that path is absolute if provided
        if path and not os.path.isabs(path):
            raise HTTPException(status_code=400, detail="Path must be absolute")

        # Get original project info for the response
        old_project = await project_repository.get_by_id(project_id)
        if not old_project:
            raise HTTPException(status_code=404, detail=f"Project with ID {project_id} not found")

        old_project_info = ProjectItem(
            id=old_project.id,
            name=old_project.name,
            path=old_project.path,
            is_default=old_project.is_default or False,
        )

        # Update using project name (service layer still uses names internally)
        if path:
            await project_service.move_project(old_project.name, path)
        elif is_active is not None:
            await project_service.update_project(old_project.name, is_active=is_active)

        # Get updated project info
        updated_project = await project_repository.get_by_id(project_id)
        if not updated_project:
            raise HTTPException(
                status_code=404, detail=f"Project with ID {project_id} not found after update"
            )

        return ProjectStatusResponse(
            message=f"Project '{updated_project.name}' updated successfully",
            status="success",
            default=(old_project.name == project_service.default_project),
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


@router.delete("/{project_id}", response_model=ProjectStatusResponse)
async def delete_project_by_id(
    project_id: ProjectIdPathDep,
    project_service: ProjectServiceDep,
    project_repository: ProjectRepositoryDep,
    delete_notes: bool = Query(
        False, description="If True, delete project directory from filesystem"
    ),
) -> ProjectStatusResponse:
    """Delete a project by ID.

    Args:
        project_id: Numeric project ID
        delete_notes: If True, delete the project directory from the filesystem

    Returns:
        Response confirming the project was deleted

    Raises:
        HTTPException: 400 if trying to delete default project, 404 if not found

    Example:
        DELETE /v2/projects/3?delete_notes=false
    """
    logger.info(
        f"API v2 request: delete_project_by_id for project_id={project_id}, delete_notes={delete_notes}"
    )

    try:
        old_project = await project_repository.get_by_id(project_id)
        if not old_project:
            raise HTTPException(status_code=404, detail=f"Project with ID {project_id} not found")

        # Check if trying to delete the default project
        if old_project.name == project_service.default_project:
            available_projects = await project_service.list_projects()
            other_projects = [p.name for p in available_projects if p.id != project_id]
            detail = f"Cannot delete default project '{old_project.name}'. "
            if other_projects:
                detail += (
                    f"Set another project as default first. Available: {', '.join(other_projects)}"
                )
            else:
                detail += "This is the only project in your configuration."
            raise HTTPException(status_code=400, detail=detail)

        # Delete using project name (service layer still uses names internally)
        await project_service.remove_project(old_project.name, delete_notes=delete_notes)

        return ProjectStatusResponse(
            message=f"Project '{old_project.name}' removed successfully",
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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{project_id}/default", response_model=ProjectStatusResponse)
async def set_default_project_by_id(
    project_id: ProjectIdPathDep,
    project_service: ProjectServiceDep,
    project_repository: ProjectRepositoryDep,
) -> ProjectStatusResponse:
    """Set a project as the default project by ID.

    Args:
        project_id: Numeric project ID to set as default

    Returns:
        Response confirming the project was set as default

    Raises:
        HTTPException: 404 if project not found

    Example:
        PUT /v2/projects/3/default
    """
    logger.info(f"API v2 request: set_default_project_by_id for project_id={project_id}")

    try:
        # Get the old default project
        default_name = project_service.default_project
        default_project = await project_service.get_project(default_name)
        if not default_project:
            raise HTTPException(
                status_code=404, detail=f"Default Project: '{default_name}' does not exist"
            )

        # Get the new default project
        new_default_project = await project_repository.get_by_id(project_id)
        if not new_default_project:
            raise HTTPException(status_code=404, detail=f"Project with ID {project_id} not found")

        # Set as default using project name (service layer still uses names internally)
        await project_service.set_default_project(new_default_project.name)

        return ProjectStatusResponse(
            message=f"Project '{new_default_project.name}' set as default successfully",
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
                name=new_default_project.name,
                path=new_default_project.path,
                is_default=True,
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
