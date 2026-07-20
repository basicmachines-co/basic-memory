"""Repository dependency injection for basic-memory.

This module provides repository dependencies:
- EntityRepository
- ObservationRepository
- RelationRepository
- SearchRepository

Each repository is scoped to the project resolved from the external UUID in the
request path (the only resolution tier since the v1 routers were removed, #1109).
"""

from typing import Annotated

from fastapi import Depends

from basic_memory.deps.config import AppConfigDep
from basic_memory.deps.db import SessionMakerDep
from basic_memory.deps.projects import ProjectExternalIdPathDep
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import SearchRepository, create_search_repository


# --- Entity Repository ---


async def get_entity_repository_v2_external(
    project_id: ProjectExternalIdPathDep,
) -> EntityRepository:
    """Create an EntityRepository instance for v2 API (uses external_id from path)."""
    return EntityRepository(project_id=project_id)


EntityRepositoryV2ExternalDep = Annotated[
    EntityRepository, Depends(get_entity_repository_v2_external)
]


# --- Observation Repository ---


async def get_observation_repository_v2_external(
    project_id: ProjectExternalIdPathDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for v2 API (uses external_id)."""
    return ObservationRepository(project_id=project_id)


ObservationRepositoryV2ExternalDep = Annotated[
    ObservationRepository, Depends(get_observation_repository_v2_external)
]


# --- Relation Repository ---


async def get_relation_repository_v2_external(
    project_id: ProjectExternalIdPathDep,
) -> RelationRepository:
    """Create a RelationRepository instance for v2 API (uses external_id)."""
    return RelationRepository(project_id=project_id)


RelationRepositoryV2ExternalDep = Annotated[
    RelationRepository, Depends(get_relation_repository_v2_external)
]


# --- Search Repository ---


async def get_search_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
    app_config: AppConfigDep,
) -> SearchRepository:
    """Create a backend-specific SearchRepository instance for the current project.

    Uses factory function to return SQLiteSearchRepository or PostgresSearchRepository
    based on database backend configuration.
    """
    return create_search_repository(session_maker, project_id=project_id, app_config=app_config)


SearchRepositoryV2ExternalDep = Annotated[
    SearchRepository, Depends(get_search_repository_v2_external)
]
