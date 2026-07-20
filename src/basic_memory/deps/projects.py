"""Project dependency injection for basic-memory.

This module provides project-related dependencies:
- Project resolution from the external UUID in the URL path
- Project config resolution
- Project repository

The v2 API is the only public surface, and it addresses projects exclusively by
external UUID; the name-based (v1) and integer-id resolution tiers were removed
with the v1 routers (#1109).
"""

import pathlib
from typing import Annotated

from fastapi import Depends, HTTPException, status

from basic_memory import db
from basic_memory.config import ProjectConfig
from basic_memory.deps.db import SessionMakerDep
from basic_memory.repository.project_repository import ProjectRepository


# --- Project Repository ---


async def get_project_repository() -> ProjectRepository:
    """Get the project repository."""
    return ProjectRepository()


ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]


# --- V2 API: External UUID Project ID from Path ---


async def validate_project_external_id(
    session_maker: SessionMakerDep,
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
    async with db.scoped_session(session_maker) as session:
        project_obj = await project_repository.get_by_external_id(session, project_id)
        if not project_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project with external_id '{project_id}' not found.",
            )
    return project_obj.id


ProjectExternalIdPathDep = Annotated[int, Depends(validate_project_external_id)]


async def get_project_config_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
    project_repository: ProjectRepositoryDep,
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
    async with db.scoped_session(session_maker) as session:
        project_obj = await project_repository.get_by_id(session, project_id)
        if project_obj:
            return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found (this should not happen since ProjectExternalIdPathDep already validates)
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with ID {project_id} not found."
    )


ProjectConfigV2ExternalDep = Annotated[ProjectConfig, Depends(get_project_config_v2_external)]
