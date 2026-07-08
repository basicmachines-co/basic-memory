"""Portable project-index runtime facade.

Cloud and local runtimes still own their concrete storage, queue, and session
setup. This module owns the typed orchestration layer that can run once those
capabilities are supplied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.forward_reference_resolution import (
    ForwardReferenceEntityIndexer,
    ForwardReferenceEntityRepository,
    ForwardReferenceEntityRefreshFailure,
    ForwardReferenceEntityRefreshRun,
    ForwardReferenceEntityRefreshRuntime,
    ForwardReferenceLinkResolver,
    ForwardReferenceRelationSource,
    ForwardReferenceResolutionRun,
    ForwardReferenceResolutionRuntime,
    RepositoryForwardReferenceEntityRefreshRuntime,
    RepositoryForwardReferenceRelationSource,
    RepositoryForwardReferenceResolutionRuntime,
    run_forward_reference_entity_refresh,
    run_forward_reference_resolution,
)
from basic_memory.indexing.progress import VectorSyncProgress
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexDeleteBatchStore,
    ProjectIndexDeleteRun,
    ProjectIndexMoveBatchStore,
    ProjectIndexMoveRun,
    RepositoryProjectIndexMaintenanceStore,
    run_project_index_delete_batches,
    run_project_index_move_batches,
)
from basic_memory.indexing.vector_sync_planning import (
    CheckpointPhase,
    EntityId,
    RepositoryVectorSyncEntitySource,
    VectorSyncEntitySource,
    VectorSyncExecutor,
    VectorSyncLogger,
    plan_vector_sync_progress,
    run_vector_sync,
)
from basic_memory.runtime.storage import ProjectId


@dataclass(frozen=True, slots=True)
class ProjectIndexForwardReferenceRun:
    """Result of resolving and refreshing deferred forward-reference targets."""

    resolution: ForwardReferenceResolutionRun
    refresh: ForwardReferenceEntityRefreshRun

    @property
    def initial_count(self) -> int:
        """Return how many unresolved relations were considered."""
        return self.resolution.unresolved_before

    @property
    def unique_link_text_count(self) -> int:
        """Return how many unique unresolved link texts were considered."""
        return len(self.resolution.link_texts)

    @property
    def resolved_link_text_count(self) -> int:
        """Return how many unique link texts resolved to a target."""
        return self.resolution.resolved_link_text_count

    @property
    def resolved_count(self) -> int:
        """Return how many relation rows were updated."""
        return self.resolution.resolved_count

    @property
    def remaining_count(self) -> int:
        """Return how many initially unresolved relation rows remain unresolved."""
        return self.resolution.remaining_count

    @property
    def entity_ids_to_refresh(self) -> frozenset[EntityId]:
        """Return exact target entities selected for search refresh."""
        return self.resolution.entity_ids_to_refresh

    @property
    def successful_reindexed_entity_ids(self) -> frozenset[EntityId]:
        """Return target entities whose search rows were refreshed."""
        return self.refresh.successful_entity_ids

    @property
    def refresh_failures(self) -> tuple[ForwardReferenceEntityRefreshFailure, ...]:
        """Return target entities whose search refresh raised."""
        return self.refresh.failures


@dataclass(frozen=True, slots=True)
class ProjectIndexRuntime:
    """Portable project-index operations over injected runtime capabilities."""

    project_id: ProjectId
    vector_sync: VectorSyncExecutor
    vector_entity_source: VectorSyncEntitySource
    move_store: ProjectIndexMoveBatchStore
    delete_store: ProjectIndexDeleteBatchStore
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
        logger: VectorSyncLogger,
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
        return await run_project_index_move_batches(
            moved_files=dict(moved_files),
            batch_size=batch_size,
            move_store=self.move_store,
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
    ) -> ProjectIndexDeleteRun:
        """Delete file-backed entities for the current project."""
        return await run_project_index_delete_batches(
            deleted_paths=list(deleted_paths),
            batch_size=batch_size,
            delete_store=self.delete_store,
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
    entity_repository: ForwardReferenceEntityRepository,
    entity_indexer: ForwardReferenceEntityIndexer,
    link_resolver: ForwardReferenceLinkResolver,
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
        move_store=maintenance_store,
        delete_store=maintenance_store,
        forward_reference_relation_source=RepositoryForwardReferenceRelationSource(
            session_maker=session_maker,
            project_id=project_id,
        ),
        forward_reference_resolution_runtime=RepositoryForwardReferenceResolutionRuntime(
            session_maker=session_maker,
            link_resolver=link_resolver,
        ),
        forward_reference_entity_refresher=RepositoryForwardReferenceEntityRefreshRuntime(
            session_maker=session_maker,
            entity_repository=entity_repository,
            entity_indexer=entity_indexer,
        ),
    )
