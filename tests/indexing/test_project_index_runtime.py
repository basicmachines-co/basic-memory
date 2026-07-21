"""Tests for the portable project-index runtime facade."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.forward_reference_resolution import (
    ForwardReferenceUpdate,
)
from basic_memory.indexing.progress import VectorSyncProgress
from basic_memory.indexing.project_index_runtime import (
    ProjectIndexRuntime,
    build_default_project_index_runtime,
)
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexDeleteBatch,
    ProjectIndexDeleteBatchResult,
    ProjectIndexMoveBatch,
    ProjectIndexMoveBatchResult,
    RepositoryProjectIndexMaintenanceStore,
    StoreProjectIndexMaintenanceRunner,
)
from basic_memory.indexing.embedding_index_planning import RepositoryVectorSyncEntitySource
from basic_memory.indexing.forward_reference_resolution import (
    RepositoryForwardReferenceEntityRefreshRuntime,
    RepositoryForwardReferenceRelationSource,
    RepositoryForwardReferenceResolutionRuntime,
)


# Not frozen: UnresolvedRelation declares plain (writable) attribute members.
@dataclass(slots=True)
class StubUnresolvedRelation:
    id: int
    from_id: int
    to_name: str
    relation_type: str = "related_to"


@dataclass(slots=True)
class RecordingVectorEntitySource:
    project_entity_ids: list[int] = field(default_factory=list)
    markdown_entity_ids: list[int] = field(default_factory=list)
    markdown_filter: set[int] = field(default_factory=set)

    async def list_project_entity_ids(self) -> list[int]:
        return list(self.project_entity_ids)

    async def list_markdown_entity_ids(self) -> list[int]:
        return list(self.markdown_entity_ids)

    async def filter_markdown_entity_ids(self, entity_ids: set[int]) -> set[int]:
        return set(entity_ids & self.markdown_filter)


def _make_maintenance_store() -> RepositoryProjectIndexMaintenanceStore:
    """Build the concrete maintenance store with a never-used session maker.

    The runtime facade only ever holds a RepositoryProjectIndexMaintenanceStore, so
    these tests inject the real concrete and monkeypatch its batch methods when they
    need to observe delegation without a database.
    """
    return RepositoryProjectIndexMaintenanceStore(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        project_id=7,
    )


def _recording_move_store(
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[ProjectIndexMoveBatch],
) -> RepositoryProjectIndexMaintenanceStore:
    async def fake_apply_move_batch(
        _self: RepositoryProjectIndexMaintenanceStore,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        recorded.append(move_batch)
        return ProjectIndexMoveBatchResult(updated_files=len(move_batch.targets))

    monkeypatch.setattr(
        RepositoryProjectIndexMaintenanceStore,
        "apply_project_index_move_batch",
        fake_apply_move_batch,
    )
    return _make_maintenance_store()


def _recording_delete_store(
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[ProjectIndexDeleteBatch],
) -> RepositoryProjectIndexMaintenanceStore:
    async def fake_apply_delete_batch(
        _self: RepositoryProjectIndexMaintenanceStore,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        recorded.append(delete_batch)
        return ProjectIndexDeleteBatchResult(
            deleted_entities=len(delete_batch.paths),
            relation_cleanup_entity_ids=frozenset({100 + delete_batch.completed_batches}),
        )

    monkeypatch.setattr(
        RepositoryProjectIndexMaintenanceStore,
        "apply_project_index_delete_batch",
        fake_apply_delete_batch,
    )
    return _make_maintenance_store()


@dataclass(slots=True)
class RecordingForwardReferenceRelationSource:
    relations: tuple[StubUnresolvedRelation, ...]

    async def list_unresolved_forward_references(
        self,
    ) -> tuple[StubUnresolvedRelation, ...]:
        return self.relations


@dataclass(slots=True)
class RecordingForwardReferenceResolutionRuntime:
    resolved_targets: dict[str, int | None]
    resolve_calls: list[tuple[str, ...]] = field(default_factory=list)
    applied_updates: tuple[ForwardReferenceUpdate, ...] = ()

    async def resolve_forward_reference_link_texts(
        self,
        link_texts: Sequence[str],
    ) -> dict[str, int | None]:
        self.resolve_calls.append(tuple(link_texts))
        return self.resolved_targets

    async def apply_forward_reference_updates(
        self,
        updates: Sequence[ForwardReferenceUpdate],
    ) -> None:
        self.applied_updates = tuple(updates)


@dataclass(slots=True)
class RecordingForwardReferenceEntityRefresher:
    outcomes: dict[int, bool | Exception]
    calls: list[int] = field(default_factory=list)

    async def refresh_forward_reference_entity(self, entity_id: int) -> bool:
        self.calls.append(entity_id)
        outcome = self.outcomes[entity_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass(slots=True)
class NoopVectorSync:
    async def sync_entity_vectors_batch(self, entity_ids, progress_callback=None):
        msg = "sync_entity_vectors_batch should not be called by these tests"
        raise AssertionError(msg)


@dataclass(slots=True)
class NoopEntityRepository:
    async def find_by_id(self, session, entity_id: int):
        msg = "find_by_id should not be called by the construction test"
        raise AssertionError(msg)


@dataclass(slots=True)
class NoopEntityIndexer:
    async def index_entity(self, entity) -> None:
        msg = "index_entity should not be called by the construction test"
        raise AssertionError(msg)


def make_runtime(
    *,
    vector_entity_source: RecordingVectorEntitySource | None = None,
    move_store: RepositoryProjectIndexMaintenanceStore | None = None,
    delete_store: RepositoryProjectIndexMaintenanceStore | None = None,
    relation_source: RecordingForwardReferenceRelationSource | None = None,
    resolution_runtime: RecordingForwardReferenceResolutionRuntime | None = None,
    refresher: RecordingForwardReferenceEntityRefresher | None = None,
) -> ProjectIndexRuntime:
    return ProjectIndexRuntime(
        project_id=7,
        vector_sync=NoopVectorSync(),
        vector_entity_source=vector_entity_source or RecordingVectorEntitySource(),
        maintenance=StoreProjectIndexMaintenanceRunner(
            move_store=move_store or _make_maintenance_store(),
            delete_store=delete_store or _make_maintenance_store(),
        ),
        forward_reference_relation_source=relation_source
        or RecordingForwardReferenceRelationSource(relations=()),
        forward_reference_resolution_runtime=resolution_runtime
        or RecordingForwardReferenceResolutionRuntime(resolved_targets={}),
        forward_reference_entity_refresher=refresher
        or RecordingForwardReferenceEntityRefresher(outcomes={}),
    )


def test_build_default_project_index_runtime_composes_repository_backed_runtime() -> None:
    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker()
    vector_sync = NoopVectorSync()
    entity_repository = NoopEntityRepository()
    entity_indexer = NoopEntityIndexer()

    runtime = build_default_project_index_runtime(
        project_id=7,
        session_maker=session_maker,
        vector_sync=vector_sync,
        entity_repository=entity_repository,
        entity_indexer=entity_indexer,
    )

    assert isinstance(runtime, ProjectIndexRuntime)
    assert runtime.project_id == 7
    assert runtime.vector_sync is vector_sync
    assert isinstance(runtime.vector_entity_source, RepositoryVectorSyncEntitySource)
    assert runtime.vector_entity_source.session_maker is session_maker
    assert runtime.vector_entity_source.project_id == 7
    assert isinstance(runtime.maintenance, StoreProjectIndexMaintenanceRunner)
    assert isinstance(runtime.maintenance.move_store, RepositoryProjectIndexMaintenanceStore)
    assert runtime.maintenance.move_store is runtime.maintenance.delete_store
    assert isinstance(
        runtime.forward_reference_relation_source,
        RepositoryForwardReferenceRelationSource,
    )
    assert isinstance(
        runtime.forward_reference_resolution_runtime,
        RepositoryForwardReferenceResolutionRuntime,
    )
    assert runtime.forward_reference_resolution_runtime.session_maker is session_maker
    assert runtime.forward_reference_resolution_runtime.project_id == 7
    assert isinstance(
        runtime.forward_reference_entity_refresher,
        RepositoryForwardReferenceEntityRefreshRuntime,
    )
    assert runtime.forward_reference_entity_refresher.entity_repository is entity_repository
    assert runtime.forward_reference_entity_refresher.entity_indexer is entity_indexer


def test_project_index_runtime_plans_vector_sync_candidates() -> None:
    runtime = make_runtime()
    resume_progress = VectorSyncProgress(entity_ids=[11, 22])

    planned = runtime.plan_vector_sync_progress(
        checkpoint_phase="forward_refs_complete",
        indexed_entity_ids={33, 11},
        relation_cleanup_entity_ids={44},
        forward_ref_reindexed_entity_ids={22, 55},
        resume_progress=resume_progress,
    )

    assert planned is resume_progress
    assert planned.entity_ids == [11, 22, 33, 44, 55]


@pytest.mark.asyncio
async def test_project_index_runtime_delegates_move_and_delete_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_move_batches: list[ProjectIndexMoveBatch] = []
    recorded_delete_batches: list[ProjectIndexDeleteBatch] = []
    move_store = _recording_move_store(monkeypatch, recorded_move_batches)
    delete_store = _recording_delete_store(monkeypatch, recorded_delete_batches)

    runtime = make_runtime(move_store=move_store, delete_store=delete_store)

    move_run = await runtime.run_move_batches(
        moved_files={"old.md": "new.md", "a.md": "b.md"},
        batch_size=1,
    )
    delete_run = await runtime.run_delete_batches(
        deleted_paths=["gone.md", "missing.md"],
        batch_size=2,
    )

    assert [
        [(target.old_path, target.new_path) for target in batch.targets]
        for batch in recorded_move_batches
    ] == [
        [("old.md", "new.md")],
        [("a.md", "b.md")],
    ]
    assert move_run.total_moves == 2
    assert move_run.total_updated_files == 2
    assert recorded_delete_batches[0].paths == ("gone.md", "missing.md")
    assert delete_run.total_deleted_entities == 2
    assert delete_run.relation_cleanup_entity_ids == frozenset({101})


@pytest.mark.asyncio
async def test_project_index_runtime_resolves_forward_refs_and_refreshes_targets() -> None:
    relation_source = RecordingForwardReferenceRelationSource(
        relations=(
            StubUnresolvedRelation(id=1, from_id=10, to_name="Target"),
            StubUnresolvedRelation(id=2, from_id=11, to_name="Broken"),
            StubUnresolvedRelation(id=3, from_id=12, to_name="Fails"),
        )
    )
    resolution_runtime = RecordingForwardReferenceResolutionRuntime(
        resolved_targets={
            "Target": 100,
            "Broken": None,
            "Fails": 200,
        }
    )
    refresher = RecordingForwardReferenceEntityRefresher(
        outcomes={
            100: True,
            200: RuntimeError("refresh failed"),
        }
    )
    runtime = make_runtime(
        relation_source=relation_source,
        resolution_runtime=resolution_runtime,
        refresher=refresher,
    )

    run = await runtime.resolve_forward_references()

    assert resolution_runtime.resolve_calls == [("Target", "Broken", "Fails")]
    assert resolution_runtime.applied_updates == (
        ForwardReferenceUpdate(
            relation_id=1,
            source_entity_id=10,
            target_entity_id=100,
            link_text="Target",
        ),
        ForwardReferenceUpdate(
            relation_id=3,
            source_entity_id=12,
            target_entity_id=200,
            link_text="Fails",
        ),
    )
    assert set(refresher.calls) == {100, 200}
    assert run.resolution.unresolved_before == 3
    assert run.resolution.link_texts == ("Target", "Broken", "Fails")
    assert run.resolution.resolved_link_text_count == 2
    assert run.resolution.resolved_count == 2
    assert run.resolution.remaining_count == 1
    assert run.resolution.entity_ids_to_refresh == frozenset({100, 200})
    assert run.refresh.successful_entity_ids == frozenset({100})
    assert [failure.entity_id for failure in run.refresh.failures] == [200]
