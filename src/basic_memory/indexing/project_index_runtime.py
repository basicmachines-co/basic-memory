"""Portable project-index runtime facade.

Cloud and local runtimes still own their concrete storage, queue, and session
setup. This module owns the typed orchestration layer that can run once those
capabilities are supplied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.forward_reference_resolution import (
    ForwardReferenceEntityRefreshRun,
    ForwardReferenceEntityRefreshRuntime,
    ForwardReferenceRelationSource,
    ForwardReferenceResolutionRun,
    ForwardReferenceResolutionRuntime,
    RepositoryForwardReferenceEntityRefreshRuntime,
    RepositoryForwardReferenceRelationSource,
    RepositoryForwardReferenceResolutionRuntime,
    run_forward_reference_entity_refresh,
    run_forward_reference_resolution,
)
from basic_memory.indexing.relation_resolution import (
    RelationResolutionEntityIndexer,
    RelationResolutionEntityRepository,
)
from basic_memory.indexing.progress import VectorSyncProgress
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexDeleteRun,
    ProjectIndexMaintenanceRunner,
    ProjectIndexMoveRun,
    RepositoryProjectIndexMaintenanceStore,
    StoreProjectIndexMaintenanceRunner,
)
from basic_memory.indexing.vector_sync_planning import (
    CheckpointPhase,
    EntityId,
    RepositoryVectorSyncEntitySource,
    VectorSyncEntitySource,
    VectorSyncExecutor,
    plan_vector_sync_progress,
    run_vector_sync,
)
from basic_memory.runtime.storage import ProjectId

if TYPE_CHECKING:  # pragma: no cover
    from loguru import Logger


@dataclass(frozen=True, slots=True)
class ProjectIndexForwardReferenceRun:
    """Result of resolving and refreshing deferred forward-reference targets."""

    resolution: ForwardReferenceResolutionRun
    refresh: ForwardReferenceEntityRefreshRun


@dataclass(frozen=True, slots=True)
class ProjectIndexRuntime:
    """Portable project-index operations over injected runtime capabilities."""

    project_id: ProjectId
    vector_sync: VectorSyncExecutor
    vector_entity_source: VectorSyncEntitySource
    maintenance: ProjectIndexMaintenanceRunner
    forward_reference_relation_source: ForwardReferenceRelationSource
    forward_reference_resolution_runtime: ForwardReferenceResolutionRuntime
    forward_reference_entity_refresher: ForwardReferenceEntityRefreshRuntime

    async def list_project_entity_ids(self) -> list[EntityId]:
        """Return all entity ids for this project in stable order."""
        return await self.vector_entity_source.list_project_entity_ids()

    async def list_markdown_entity_ids(self) -> list[EntityId]:
        """Return markdown entity ids for this project in stable order."""
        return await self.vector_entity_source.list_markdown_entity_ids()

    async def filter_markdown_entity_ids(self, entity_ids: set[EntityId]) -> set[EntityId]:
        """Keep only markdown entity ids from a planned vector-sync candidate set."""
        return await self.vector_entity_source.filter_markdown_entity_ids(entity_ids)

    def plan_vector_sync_progress(
        self,
        *,
        checkpoint_phase: CheckpointPhase,
        indexed_entity_ids: set[EntityId],
        relation_cleanup_entity_ids: set[EntityId],
        forward_ref_reindexed_entity_ids: set[EntityId],
        resume_progress: VectorSyncProgress,
    ) -> VectorSyncProgress:
        """Build the durable vector-sync plan for the current indexing run."""
        candidate_entity_ids = sorted(
            indexed_entity_ids | relation_cleanup_entity_ids | forward_ref_reindexed_entity_ids
        )
        return plan_vector_sync_progress(
            checkpoint_phase=checkpoint_phase,
            candidate_entity_ids=candidate_entity_ids,
            resume_progress=resume_progress,
        )

    async def sync_entity_vectors(
        self,
        entity_ids: Sequence[EntityId],
        *,
        logger: Logger,
        resume_progress: VectorSyncProgress | None = None,
    ) -> VectorSyncProgress:
        """Sync semantic vectors for the given entity ids."""
        return await run_vector_sync(
            entity_ids,
            vector_sync=self.vector_sync,
            logger=logger,
            resume_progress=resume_progress,
            project_id=self.project_id,
        )

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
    ) -> ProjectIndexMoveRun:
        """Apply moved-file updates for the current project."""
        return await self.maintenance.run_move_batches(
            moved_files=moved_files,
            batch_size=batch_size,
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
    ) -> ProjectIndexDeleteRun:
        """Delete file-backed entities for the current project."""
        return await self.maintenance.run_delete_batches(
            deleted_paths=deleted_paths,
            batch_size=batch_size,
        )

    async def resolve_forward_references(self) -> ProjectIndexForwardReferenceRun:
        """Resolve outstanding cross-batch note links and refresh affected targets."""
        unresolved_relations = (
            await self.forward_reference_relation_source.list_unresolved_forward_references()
        )
        resolution = await run_forward_reference_resolution(
            self.forward_reference_resolution_runtime,
            unresolved_relations,
        )
        refresh = await run_forward_reference_entity_refresh(
            self.forward_reference_entity_refresher,
            resolution.entity_ids_to_refresh,
        )
        return ProjectIndexForwardReferenceRun(
            resolution=resolution,
            refresh=refresh,
        )


def build_default_project_index_runtime(
    *,
    project_id: ProjectId,
    session_maker: async_sessionmaker[AsyncSession],
    vector_sync: VectorSyncExecutor,
    entity_repository: RelationResolutionEntityRepository,
    entity_indexer: RelationResolutionEntityIndexer,
) -> ProjectIndexRuntime:
    """Compose the default repository-backed project-index runtime."""
    vector_entity_source = RepositoryVectorSyncEntitySource(
        session_maker=session_maker,
        project_id=project_id,
    )
    maintenance_store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=project_id,
    )
    return ProjectIndexRuntime(
        project_id=project_id,
        vector_sync=vector_sync,
        vector_entity_source=vector_entity_source,
        maintenance=StoreProjectIndexMaintenanceRunner(
            move_store=maintenance_store,
            delete_store=maintenance_store,
        ),
        forward_reference_relation_source=RepositoryForwardReferenceRelationSource(
            session_maker=session_maker,
            project_id=project_id,
        ),
        forward_reference_resolution_runtime=RepositoryForwardReferenceResolutionRuntime(
            session_maker=session_maker,
            project_id=project_id,
        ),
        forward_reference_entity_refresher=RepositoryForwardReferenceEntityRefreshRuntime(
            session_maker=session_maker,
            entity_repository=entity_repository,
            entity_indexer=entity_indexer,
        ),
    )
