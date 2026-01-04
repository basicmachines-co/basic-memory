"""Project dependency injection for basic-memory.

This module provides project-related dependencies:
- Project path extraction from URL
- Project config resolution
- Project ID validation
- Project repository
"""

import pathlib
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status

from basic_memory.config import ProjectConfig
from basic_memory.deps.db import SessionMakerDep
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.utils import generate_permalink


# --- Project Repository ---


async def get_project_repository(
    session_maker: SessionMakerDep,
) -> ProjectRepository:
    """Get the project repository."""
    return ProjectRepository(session_maker)


ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]


# --- Path Extraction ---

# V1 API: Project name from URL path
ProjectPathDep = Annotated[str, Path()]


# --- Project ID Resolution (V1 API) ---


async def get_project_id(
    project_repository: ProjectRepositoryDep,
    project: ProjectPathDep,
) -> int:
    """Get the current project ID from request state.

    When using sub-applications with /{project} mounting, the project value
    is stored in request.state by middleware.

    Args:
        project_repository: Repository for project operations
        project: The project name from URL path

    Returns:
        The resolved project ID

    Raises:
        HTTPException: If project is not found
    """
    # Convert project name to permalink for lookup
    project_permalink = generate_permalink(str(project))
    project_obj = await project_repository.get_by_permalink(project_permalink)
    if project_obj:
        return project_obj.id

    # Try by name if permalink lookup fails
    project_obj = await project_repository.get_by_name(str(project))  # pragma: no cover
    if project_obj:  # pragma: no cover
        return project_obj.id

    # Not found
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project}' not found."
    )


ProjectIdDep = Annotated[int, Depends(get_project_id)]


# --- Project Config Resolution (V1 API) ---


async def get_project_config(
    project: ProjectPathDep, project_repository: ProjectRepositoryDep
) -> ProjectConfig:  # pragma: no cover
    """Get the current project referenced from request state.

    Args:
        project: The project name from URL path
        project_repository: Repository for project operations

    Returns:
        The resolved project config

    Raises:
        HTTPException: If project is not found
    """
    # Convert project name to permalink for lookup
    project_permalink = generate_permalink(str(project))
    project_obj = await project_repository.get_by_permalink(project_permalink)
    if project_obj:
        return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project}' not found."
    )


ProjectConfigDep = Annotated[ProjectConfig, Depends(get_project_config)]


# --- V2 API: Integer Project ID from Path ---


async def validate_project_id(
    project_id: int,
    project_repository: ProjectRepositoryDep,
) -> int:
    """Validate that a numeric project ID exists in the database.

    This is used for v2 API endpoints that take project IDs as integers in the path.
    The project_id parameter will be automatically extracted from the URL path by FastAPI.

    Args:
        project_id: The numeric project ID from the URL path
        project_repository: Repository for project operations

    Returns:
        The validated project ID

    Raises:
        HTTPException: If project with that ID is not found
    """
    project_obj = await project_repository.get_by_id(project_id)
    if not project_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID {project_id} not found.",
        )
    return project_id


ProjectIdPathDep = Annotated[int, Depends(validate_project_id)]


async def get_project_config_v2(
    project_id: ProjectIdPathDep, project_repository: ProjectRepositoryDep
) -> ProjectConfig:  # pragma: no cover
    """Get the project config for v2 API (uses integer project_id from path).

    Args:
        project_id: The validated numeric project ID from the URL path
        project_repository: Repository for project operations

    Returns:
        The resolved project config

    Raises:
        HTTPException: If project is not found
    """
    project_obj = await project_repository.get_by_id(project_id)
    if project_obj:
        return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found (this should not happen since ProjectIdPathDep already validates existence)
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with ID {project_id} not found."
    )


ProjectConfigV2Dep = Annotated[ProjectConfig, Depends(get_project_config_v2)]


# --- V2 API: External UUID Project ID from Path ---


async def validate_project_external_id(
    project_id: str,
    project_repository: ProjectRepositoryDep,
) -> int:
    """Validate that a project external_id (UUID) exists in the database.

    This is used for v2 API endpoints that take project external_ids as strings in the path.
    The project_id parameter will be automatically extracted from the URL path by FastAPI.

    Args:
        project_id: The external UUID from the URL path (named project_id for URL consistency)
        project_repository: Repository for project operations

    Returns:
        The internal numeric project ID (for use by repositories)

    Raises:
        HTTPException: If project with that external_id is not found
    """
    project_obj = await project_repository.get_by_external_id(project_id)
    if not project_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with external_id '{project_id}' not found.",
        )
    return project_obj.id


ProjectExternalIdPathDep = Annotated[int, Depends(validate_project_external_id)]


async def get_project_config_v2_external(
    project_id: ProjectExternalIdPathDep, project_repository: ProjectRepositoryDep
) -> ProjectConfig:  # pragma: no cover
    """Get the project config for v2 API (uses external_id UUID from path).

    Args:
        project_id: The internal project ID resolved from external_id
        project_repository: Repository for project operations

    Returns:
        The resolved project config

    Raises:
        HTTPException: If project is not found
    """
    project_obj = await project_repository.get_by_id(project_id)
    if project_obj:
        return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found (this should not happen since ProjectExternalIdPathDep already validates)
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with ID {project_id} not found."
    )


ProjectConfigV2ExternalDep = Annotated[
    ProjectConfig, Depends(get_project_config_v2_external)
]
