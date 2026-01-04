"""Repository dependency injection for basic-memory.

This module provides repository dependencies:
- EntityRepository
- ObservationRepository
- RelationRepository
- SearchRepository

Each repository is scoped to a project ID from the request.
"""

from typing import Annotated

from fastapi import Depends

from basic_memory.deps.db import SessionMakerDep
from basic_memory.deps.projects import (
    ProjectIdDep,
    ProjectIdPathDep,
    ProjectExternalIdPathDep,
)
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import SearchRepository, create_search_repository


# --- Entity Repository ---


async def get_entity_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> EntityRepository:
    """Create an EntityRepository instance for the current project."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryDep = Annotated[EntityRepository, Depends(get_entity_repository)]


async def get_entity_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> EntityRepository:
    """Create an EntityRepository instance for v2 API (uses integer project_id from path)."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryV2Dep = Annotated[EntityRepository, Depends(get_entity_repository_v2)]


async def get_entity_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> EntityRepository:
    """Create an EntityRepository instance for v2 API (uses external_id from path)."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryV2ExternalDep = Annotated[EntityRepository, Depends(get_entity_repository_v2_external)]


# --- Observation Repository ---


async def get_observation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for the current project."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryDep = Annotated[ObservationRepository, Depends(get_observation_repository)]


async def get_observation_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for v2 API."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryV2Dep = Annotated[
    ObservationRepository, Depends(get_observation_repository_v2)
]


async def get_observation_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for v2 API (uses external_id)."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryV2ExternalDep = Annotated[
    ObservationRepository, Depends(get_observation_repository_v2_external)
]


# --- Relation Repository ---


async def get_relation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> RelationRepository:
    """Create a RelationRepository instance for the current project."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryDep = Annotated[RelationRepository, Depends(get_relation_repository)]


async def get_relation_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> RelationRepository:
    """Create a RelationRepository instance for v2 API."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryV2Dep = Annotated[RelationRepository, Depends(get_relation_repository_v2)]


async def get_relation_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> RelationRepository:
    """Create a RelationRepository instance for v2 API (uses external_id)."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryV2ExternalDep = Annotated[
    RelationRepository, Depends(get_relation_repository_v2_external)
]


# --- Search Repository ---


async def get_search_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> SearchRepository:
    """Create a backend-specific SearchRepository instance for the current project.

    Uses factory function to return SQLiteSearchRepository or PostgresSearchRepository
    based on database backend configuration.
    """
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryDep = Annotated[SearchRepository, Depends(get_search_repository)]


async def get_search_repository_v2(  # pragma: no cover
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> SearchRepository:
    """Create a SearchRepository instance for v2 API."""
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryV2Dep = Annotated[SearchRepository, Depends(get_search_repository_v2)]


async def get_search_repository_v2_external(
    session_maker: SessionMakerDep,
    project_id: ProjectExternalIdPathDep,
) -> SearchRepository:
    """Create a SearchRepository instance for v2 API (uses external_id)."""
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryV2ExternalDep = Annotated[SearchRepository, Depends(get_search_repository_v2_external)]
