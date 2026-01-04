"""V2 Project Router - External ID-based project management operations.

This router provides external_id (UUID) based CRUD operations for projects,
using stable string UUIDs that never change (unlike integer IDs or names).

Key improvements:
- Stable external UUIDs that won't change with renames or database migrations
- Better API ergonomics with consistent string identifiers
- Direct database lookups via unique indexed column
- Consistent with v2 entity operations
"""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Body, Query, Path
from loguru import logger

from basic_memory.deps import (
    ProjectServiceDep,
    ProjectRepositoryDep,
)
from basic_memory.schemas.project_info import (
    ProjectItem,
    ProjectStatusResponse,
)
from basic_memory.schemas.v2 import ProjectResolveRequest, ProjectResolveResponse
from basic_memory.utils import normalize_project_path, generate_permalink

router = APIRouter(prefix="/projects", tags=["project_management-v2"])


@router.post("/resolve", response_model=ProjectResolveResponse)
async def resolve_project_identifier(
    data: ProjectResolveRequest,
    project_repository: ProjectRepositoryDep,
) -> ProjectResolveResponse:
    """Resolve a project identifier (name, permalink, or external_id) to project info.

    This endpoint provides efficient lookup of projects by various identifiers
    without needing to fetch the entire project list. Supports:
    - External ID (UUID string) - preferred stable identifier
    - Permalink
    - Case-insensitive name matching

    Args:
        data: Request containing the identifier to resolve

    Returns:
        Project information including the external_id (UUID)

    Raises:
        HTTPException: 404 if project not found

    Example:
        POST /v2/projects/resolve
        {"identifier": "my-project"}

        Returns:
        {
            "external_id": "550e8400-e29b-41d4-a716-446655440000",
            "project_id": 1,
            "name": "my-project",
            "permalink": "my-project",
            "path": "/path/to/project",
            "is_active": true,
            "is_default": false,
            "resolution_method": "name"
        }
    """
    logger.info(f"API v2 request: resolve_project_identifier for '{data.identifier}'")

    # Generate permalink for comparison
    identifier_permalink = generate_permalink(data.identifier)

    resolution_method = "name"
    project = None

    # Try external_id first (UUID format)
    project = await project_repository.get_by_external_id(data.identifier)
    if project:
        resolution_method = "external_id"

    # If not found by external_id, try by permalink (exact match)
    if not project:
        project = await project_repository.get_by_permalink(identifier_permalink)
        if project:
            resolution_method = "permalink"

    # If not found by permalink, try case-insensitive name search
    if not project:
        project = await project_repository.get_by_name_case_insensitive(data.identifier)
        if project:
            resolution_method = "name"  # pragma: no cover

    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: '{data.identifier}'")

    return ProjectResolveResponse(
        external_id=project.external_id,
        project_id=project.id,
        name=project.name,
        permalink=generate_permalink(project.name),
        path=normalize_project_path(project.path),
        is_active=project.is_active if hasattr(project, "is_active") else True,
        is_default=project.is_default or False,
        resolution_method=resolution_method,
    )


@router.get("/{project_id}", response_model=ProjectItem)
async def get_project_by_id(
    project_repository: ProjectRepositoryDep,
    project_id: str = Path(..., description="Project external ID (UUID)"),
) -> ProjectItem:
    """Get project by its external ID (UUID).

    This is the primary project retrieval method in v2, using stable UUID
    identifiers that won't change with project renames.

    Args:
        project_id: External ID (UUID string)

    Returns:
        Project information including external_id

    Raises:
        HTTPException: 404 if project not found

    Example:
        GET /v2/projects/550e8400-e29b-41d4-a716-446655440000
    """
    logger.info(f"API v2 request: get_project_by_id for project_id={project_id}")

    project = await project_repository.get_by_external_id(project_id)
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project with external_id '{project_id}' not found"
        )

    return ProjectItem(
        id=project.id,
        external_id=project.external_id,
        name=project.name,
        path=normalize_project_path(project.path),
        is_default=project.is_default or False,
    )


@router.patch("/{project_id}", response_model=ProjectStatusResponse)
async def update_project_by_id(
    project_service: ProjectServiceDep,
    project_repository: ProjectRepositoryDep,
    project_id: str = Path(..., description="Project external ID (UUID)"),
    path: Optional[str] = Body(None, description="New absolute path for the project"),
    is_active: Optional[bool] = Body(None, description="Status of the project (active/inactive)"),
) -> ProjectStatusResponse:
    """Update a project's information by external ID.

    Args:
        project_id: External ID (UUID string)
        path: Optional new absolute path for the project
        is_active: Optional status update for the project

    Returns:
        Response confirming the project was updated

    Raises:
        HTTPException: 400 if validation fails, 404 if project not found

    Example:
        PATCH /v2/projects/550e8400-e29b-41d4-a716-446655440000
        {"path": "/new/path"}
    """
    logger.info(f"API v2 request: update_project_by_id for project_id={project_id}")

    try:
        # Validate that path is absolute if provided
        if path and not os.path.isabs(path):
            raise HTTPException(status_code=400, detail="Path must be absolute")

        # Get original project info for the response
        old_project = await project_repository.get_by_external_id(project_id)
        if not old_project:
            raise HTTPException(
                status_code=404, detail=f"Project with external_id '{project_id}' not found"
            )

        old_project_info = ProjectItem(
            id=old_project.id,
            external_id=old_project.external_id,
            name=old_project.name,
            path=old_project.path,
            is_default=old_project.is_default or False,
        )

        # Update using project name (service layer still uses names internally)
        if path:
            await project_service.move_project(old_project.name, path)
        elif is_active is not None:
            await project_service.update_project(old_project.name, is_active=is_active)

        # Get updated project info (use the same external_id)
        updated_project = await project_repository.get_by_external_id(project_id)
        if not updated_project:  # pragma: no cover
            raise HTTPException(
                status_code=404,
                detail=f"Project with external_id '{project_id}' not found after update",
            )

        return ProjectStatusResponse(
            message=f"Project '{updated_project.name}' updated successfully",
            status="success",
            default=old_project.is_default or False,
            old_project=old_project_info,
            new_project=ProjectItem(
                id=updated_project.id,
                external_id=updated_project.external_id,
                name=updated_project.name,
                path=updated_project.path,
                is_default=updated_project.is_default or False,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))  # pragma: no cover


@router.delete("/{project_id}", response_model=ProjectStatusResponse)
async def delete_project_by_id(
    project_service: ProjectServiceDep,
    project_repository: ProjectRepositoryDep,
    project_id: str = Path(..., description="Project external ID (UUID)"),
    delete_notes: bool = Query(
        False, description="If True, delete project directory from filesystem"
    ),
) -> ProjectStatusResponse:
    """Delete a project by external ID.

    Args:
        project_id: External ID (UUID string)
        delete_notes: If True, delete the project directory from the filesystem

    Returns:
        Response confirming the project was deleted

    Raises:
        HTTPException: 400 if trying to delete default project, 404 if not found

    Example:
        DELETE /v2/projects/550e8400-e29b-41d4-a716-446655440000?delete_notes=false
    """
    logger.info(
        f"API v2 request: delete_project_by_id for project_id={project_id}, delete_notes={delete_notes}"
    )

    try:
        old_project = await project_repository.get_by_external_id(project_id)
        if not old_project:
            raise HTTPException(
                status_code=404, detail=f"Project with external_id '{project_id}' not found"
            )

        # Check if trying to delete the default project
        # Use is_default from database, not ConfigManager (which doesn't work in cloud mode)
        if old_project.is_default:
            available_projects = await project_service.list_projects()
            other_projects = [
                p.name for p in available_projects if p.external_id != project_id
            ]
            detail = f"Cannot delete default project '{old_project.name}'. "
            if other_projects:
                detail += (  # pragma: no cover
                    f"Set another project as default first. Available: {', '.join(other_projects)}"
                )
            else:
                detail += "This is the only project in your configuration."  # pragma: no cover
            raise HTTPException(status_code=400, detail=detail)

        # Delete using project name (service layer still uses names internally)
        await project_service.remove_project(old_project.name, delete_notes=delete_notes)

        return ProjectStatusResponse(
            message=f"Project '{old_project.name}' removed successfully",
            status="success",
            default=False,
            old_project=ProjectItem(
                id=old_project.id,
                external_id=old_project.external_id,
                name=old_project.name,
                path=old_project.path,
                is_default=old_project.is_default or False,
            ),
            new_project=None,
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))  # pragma: no cover


@router.put("/{project_id}/default", response_model=ProjectStatusResponse)
async def set_default_project_by_id(
    project_service: ProjectServiceDep,
    project_repository: ProjectRepositoryDep,
    project_id: str = Path(..., description="Project external ID (UUID)"),
) -> ProjectStatusResponse:
    """Set a project as the default project by external ID.

    Args:
        project_id: External ID (UUID string) to set as default

    Returns:
        Response confirming the project was set as default

    Raises:
        HTTPException: 404 if project not found

    Example:
        PUT /v2/projects/550e8400-e29b-41d4-a716-446655440000/default
    """
    logger.info(f"API v2 request: set_default_project_by_id for project_id={project_id}")

    try:
        # Get the old default project from database
        default_project = await project_repository.get_default_project()
        if not default_project:
            raise HTTPException(  # pragma: no cover
                status_code=404, detail="No default project is currently set"
            )

        # Get the new default project by external_id
        new_default_project = await project_repository.get_by_external_id(project_id)
        if not new_default_project:
            raise HTTPException(
                status_code=404, detail=f"Project with external_id '{project_id}' not found"
            )

        # Set as default using project name (service layer still uses names internally)
        await project_service.set_default_project(new_default_project.name)

        return ProjectStatusResponse(
            message=f"Project '{new_default_project.name}' set as default successfully",
            status="success",
            default=True,
            old_project=ProjectItem(
                id=default_project.id,
                external_id=default_project.external_id,
                name=default_project.name,
                path=default_project.path,
                is_default=False,
            ),
            new_project=ProjectItem(
                id=new_default_project.id,
                external_id=new_default_project.external_id,
                name=new_default_project.name,
                path=new_default_project.path,
                is_default=True,
            ),
        )
    except ValueError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))  # pragma: no cover
