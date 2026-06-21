"""Tests for portable project-index workflow request values."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

import basic_memory.indexing.project_index_workflow as project_index_workflow_module
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing import (
    IndexFileJobResult,
    IndexFileJobStatus,
    ProjectIndexCounters,
    ProjectIndexBatchJobPlan,
    ProjectIndexBatchJobActivity,
    ProjectIndexBatchJobActivityUpdate,
    ProjectIndexDeleteBatch,
    ProjectIndexDeleteBatchPlan,
    ProjectIndexDeleteBatchProgress,
    ProjectIndexDeleteBatchResult,
    ProjectIndexDeleteRun,
    ProjectIndexMoveBatch,
    ProjectIndexMoveBatchPlan,
    ProjectIndexMoveBatchProgress,
    ProjectIndexMoveBatchResult,
    ProjectIndexMoveRun,
    ProjectIndexMoveTarget,
    ProjectIndexWorkflowCompletionUpdate,
    ProjectIndexWorkflowFailureUpdate,
    ProjectIndexWorkflowProgressUpdate,
    ProjectIndexWorkflowQueued,
    ProjectIndexWorkflowRecordPlan,
    ProjectIndexWorkflowRequest,
    RepositoryProjectIndexMaintenanceStore,
    ProjectIndexStaleWorkflowPlan,
    ProjectIndexWorkflowStart,
    ProjectIndexWorkflowStartPlan,
    build_project_index_batch_activity_update,
    build_project_index_batch_job_plan,
    build_project_index_delete_batch_plan,
    build_project_index_move_batch_plan,
    build_project_index_workflow_completion_update,
    build_project_index_workflow_progress_update,
    build_project_index_workflow_queued,
    build_project_index_workflow_start,
    build_project_index_workflow_stale_failure_update,
    plan_project_index_batch_result_record,
    plan_project_index_file_result_record,
    plan_project_index_stale_workflow,
    plan_project_index_workflow_start,
    StoreProjectIndexMaintenanceRunner,
    run_project_index_delete_batches,
    run_project_index_move_batches,
)
from basic_memory.runtime import (
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeWorkflowMetadataPatch,
)


@dataclass(frozen=True, slots=True)
class ProjectIndexSource:
    tenant_id: UUID
    project_id: int
    project_external_id: str
    project_name: str | None
    project_permalink: str | None
    project_path: str
    workflow_id: UUID
    force_full: bool
    search: bool
    embeddings: bool


@dataclass(slots=True)
class RecordingMoveBatchStore:
    results: list[ProjectIndexMoveBatchResult]
    batches: list[ProjectIndexMoveBatch] = field(default_factory=list)

    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        self.batches.append(move_batch)
        return self.results.pop(0)


@dataclass(slots=True)
class RecordingDeleteBatchStore:
    results: list[ProjectIndexDeleteBatchResult]
    batches: list[ProjectIndexDeleteBatch] = field(default_factory=list)

    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        self.batches.append(delete_batch)
        return self.results.pop(0)


@dataclass(slots=True)
class RecordingProjectIndexMetadataReporter:
    progress_updates: list[RuntimeWorkflowMetadataPatch] = field(default_factory=list)

    async def report_progress(self, progress: RuntimeWorkflowMetadataPatch) -> None:
        self.progress_updates.append(progress)


class FakeProjectIndexScalarResult:
    """Minimal scalar result stand-in for repository maintenance tests."""

    def __init__(self, values: list[object]) -> None:
        self.values = values

    def __iter__(self) -> Iterator[object]:
        return iter(self.values)


class FakeProjectIndexMappingResult:
    """Minimal mapping result stand-in for repository maintenance tests."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, object]]:
        return self.rows


class FakeProjectIndexResult:
    """Minimal SQLAlchemy result stand-in for repository maintenance tests."""

    def __init__(
        self,
        *,
        scalar_values: list[object] | None = None,
        mapping_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.scalar_values = scalar_values or []
        self.mapping_rows = mapping_rows or []

    def scalars(self) -> FakeProjectIndexScalarResult:
        return FakeProjectIndexScalarResult(self.scalar_values)

    def mappings(self) -> FakeProjectIndexMappingResult:
        return FakeProjectIndexMappingResult(self.mapping_rows)


@dataclass(slots=True)
class FakeProjectIndexSession:
    """Record repository maintenance statements without a real database."""

    results: list[FakeProjectIndexResult]
    statements: list[object] = field(default_factory=list)
    params: list[object | None] = field(default_factory=list)

    async def execute(
        self,
        statement: object,
        params: object | None = None,
    ) -> FakeProjectIndexResult:
        self.statements.append(statement)
        self.params.append(params)
        if self.results:
            return self.results.pop(0)
        return FakeProjectIndexResult()


@dataclass(slots=True)
class RecordingMoveContentUpdater:
    """Record moved-file repair requests and return configured content updates."""

    updates: dict[int, project_index_workflow_module.ProjectIndexMovedFileContentUpdate]
    seen_files: list[project_index_workflow_module.ProjectIndexMovedFile] = field(
        default_factory=list
    )

    async def update_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: project_index_workflow_module.ProjectIndexMovedFile,
    ) -> project_index_workflow_module.ProjectIndexMovedFileContentUpdate | None:
        del session
        self.seen_files.append(moved_file)
        return self.updates.get(moved_file.entity_id)


def project_index_record_metadata(
    *,
    total: int,
    processed: int = 0,
    succeeded: int = 0,
    missing: int = 0,
    failed: int = 0,
    recorded_batches: list[int] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "phase": "indexing",
        "progress": f"Indexed {processed}/{total} files, {succeeded} succeeded",
        "payload": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "project_id": 42,
            "project_external_id": "external-project",
        },
        "discovery": {
            "total_files": total,
            "batch_count": 2,
            "batch_size": 50,
            "discovered_at": "2026-06-19T10:20:30+00:00",
        },
        "counters": {
            "total": total,
            "processed": processed,
            "succeeded": succeeded,
            "missing": missing,
            "failed": failed,
        },
    }
    if recorded_batches is not None:
        metadata["recorded_batches"] = recorded_batches
    return metadata


def test_project_index_workflow_request_serializes_existing_payload_metadata() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    assert request.workflow_payload_metadata() == {
        "tenant_id": str(tenant_id),
        "project_id": 42,
        "project_external_id": "external-project",
        "project_name": "Project Name",
        "project_permalink": "project-name",
        "project_path": "project",
        "force_full": True,
        "search": True,
        "embeddings": False,
    }


def test_project_index_workflow_queued_builds_metadata_event_and_logical_key() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    queued = build_project_index_workflow_queued(
        request=request,
        transport_broker="pgq",
        transport_entrypoint="index_project",
    )

    assert queued == ProjectIndexWorkflowQueued(
        logical_key=f"index-{tenant_id}-Project Name-full-search",
        metadata={
            "job_id": str(workflow_id),
            "phase": "queued",
            "progress": "queued for index",
            "payload": {
                "tenant_id": str(tenant_id),
                "project_id": 42,
                "project_external_id": "external-project",
                "project_name": "Project Name",
                "project_permalink": "project-name",
                "project_path": "project",
                "force_full": True,
                "search": True,
                "embeddings": False,
            },
            "transport": {
                "broker": "pgq",
                "entrypoint": "index_project",
            },
        },
        queued_event_data={
            "logical_key": f"index-{tenant_id}-Project Name-full-search",
            "entrypoint": "index_project",
            "phase": "queued",
            "progress": "queued for index",
            "project_id": 42,
            "project_external_id": "external-project",
            "project_name": "Project Name",
            "project_permalink": "project-name",
            "project_path": "project",
        },
    )


def test_project_index_batch_job_plan_builds_runtime_batch_requests() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=False,
            search=True,
            embeddings=False,
        )
    )
    observed_files = (
        RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=10),
        RuntimeObservedIndexFile(path="notes/b.txt", checksum="b", size=20),
        RuntimeObservedIndexFile(path="notes/c.md", checksum="c", size=30),
    )

    plan = build_project_index_batch_job_plan(
        request=request,
        observed_files=observed_files,
        batch_size=2,
    )

    assert plan == ProjectIndexBatchJobPlan(
        total_files=3,
        batch_count=2,
        batch_requests=(
            RuntimeIndexFileBatchJobRequest(
                tenant_id=tenant_id,
                project=request.project,
                workflow_id=workflow_id,
                batch_index=0,
                batch_count=2,
                file_paths=("notes/a.md", "notes/b.txt"),
                observed_files=observed_files[:2],
                index_embeddings=False,
            ),
            RuntimeIndexFileBatchJobRequest(
                tenant_id=tenant_id,
                project=request.project,
                workflow_id=workflow_id,
                batch_index=1,
                batch_count=2,
                file_paths=("notes/c.md",),
                observed_files=observed_files[2:],
                index_embeddings=False,
            ),
        ),
    )


def test_project_index_batch_activity_update_builds_last_activity_metadata() -> None:
    activity = ProjectIndexBatchJobActivity(
        batch_indexes=(1, 3),
        queued_count=1,
        picked_fresh_count=1,
        picked_stale_count=0,
    )

    update = build_project_index_batch_activity_update(
        metadata={
            "phase": "indexing",
            "progress": "Indexed 2/4 files, 2 succeeded",
            "payload": {"project_id": 42},
        },
        activity=activity,
        observed_at="2026-06-19T10:20:30+00:00",
    )

    assert activity.has_unfinished_jobs is True
    assert ProjectIndexBatchJobActivity.empty().has_unfinished_jobs is False
    assert update == ProjectIndexBatchJobActivityUpdate(
        activity=activity,
        metadata={
            "phase": "indexing",
            "progress": "Indexed 2/4 files, 2 succeeded",
            "payload": {"project_id": 42},
            "last_batch_job_activity": {
                "active_batches": [1, 3],
                "queued_count": 1,
                "picked_fresh_count": 1,
                "picked_stale_count": 0,
                "observed_at": "2026-06-19T10:20:30+00:00",
            },
        },
    )


def test_project_index_move_batch_plan_builds_batches_and_progress_metadata() -> None:
    plan = build_project_index_move_batch_plan(
        moved_files={
            "notes/a.md": "archive/a.md",
            "notes/b.md": "archive/b.md",
            "notes/c.md": "archive/c.md",
        },
        batch_size=2,
    )

    assert plan == ProjectIndexMoveBatchPlan(
        total_moves=3,
        batch_count=2,
        batches=(
            ProjectIndexMoveBatch(
                completed_batches=1,
                targets=(
                    ProjectIndexMoveTarget(
                        old_path="notes/a.md",
                        new_path="archive/a.md",
                    ),
                    ProjectIndexMoveTarget(
                        old_path="notes/b.md",
                        new_path="archive/b.md",
                    ),
                ),
            ),
            ProjectIndexMoveBatch(
                completed_batches=2,
                targets=(
                    ProjectIndexMoveTarget(
                        old_path="notes/c.md",
                        new_path="archive/c.md",
                    ),
                ),
            ),
        ),
    )
    assert ProjectIndexMoveBatchProgress(
        moved_files=plan.total_moves,
        completed_batches=plan.batches[0].completed_batches,
        total_batches=plan.batch_count,
        updated_files=2,
    ).workflow_metadata() == {
        "moved_files": 3,
        "completed_batches": 1,
        "total_batches": 2,
        "updated_files": 2,
    }


def test_project_index_delete_batch_plan_builds_batches_and_progress_metadata() -> None:
    plan = build_project_index_delete_batch_plan(
        deleted_paths=("notes/a.md", "notes/b.md", "notes/c.md"),
        batch_size=2,
    )

    assert plan == ProjectIndexDeleteBatchPlan(
        total_deletes=3,
        batch_count=2,
        batches=(
            ProjectIndexDeleteBatch(
                completed_batches=1,
                paths=("notes/a.md", "notes/b.md"),
            ),
            ProjectIndexDeleteBatch(
                completed_batches=2,
                paths=("notes/c.md",),
            ),
        ),
    )
    assert ProjectIndexDeleteBatchProgress(
        deleted_files=plan.total_deletes,
        completed_batches=plan.batches[1].completed_batches,
        total_batches=plan.batch_count,
        deleted_entities=3,
    ).workflow_metadata() == {
        "deleted_files": 3,
        "completed_batches": 2,
        "total_batches": 2,
        "deleted_entities": 3,
    }


def test_project_index_maintenance_batch_plans_require_positive_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size must be greater than zero"):
        build_project_index_move_batch_plan(moved_files={}, batch_size=0)

    with pytest.raises(ValueError, match="batch_size must be greater than zero"):
        build_project_index_delete_batch_plan(deleted_paths=(), batch_size=0)


@pytest.mark.asyncio
async def test_project_index_move_runner_applies_batches_and_reports_progress() -> None:
    store = RecordingMoveBatchStore(
        results=[
            ProjectIndexMoveBatchResult(
                updated_files=1,
                moved_entity_ids=frozenset({10}),
                replaced_entity_ids=frozenset({30}),
                relation_cleanup_entity_ids=frozenset({99}),
                missing_paths=("notes/b.md",),
            ),
            ProjectIndexMoveBatchResult(
                updated_files=1,
                moved_entity_ids=frozenset({11}),
            ),
        ]
    )
    metadata_reporter = RecordingProjectIndexMetadataReporter()

    run = await run_project_index_move_batches(
        moved_files={
            "notes/a.md": "archive/a.md",
            "notes/b.md": "archive/b.md",
            "notes/c.md": "archive/c.md",
        },
        batch_size=2,
        move_store=store,
        metadata_reporter=metadata_reporter,
    )

    assert store.batches == [
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(
                ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),
                ProjectIndexMoveTarget("notes/b.md", "archive/b.md"),
            ),
        ),
        ProjectIndexMoveBatch(
            completed_batches=2,
            targets=(ProjectIndexMoveTarget("notes/c.md", "archive/c.md"),),
        ),
    ]
    assert run == ProjectIndexMoveRun(
        total_moves=3,
        total_updated_files=2,
        records=run.records,
        moved_entity_ids=frozenset({10, 11}),
        replaced_entity_ids=frozenset({30}),
        relation_cleanup_entity_ids=frozenset({99}),
    )
    assert run.missing_paths == ("notes/b.md",)
    assert metadata_reporter.progress_updates == [
        {
            "moved_files": 3,
            "completed_batches": 1,
            "total_batches": 2,
            "updated_files": 1,
        },
        {
            "moved_files": 3,
            "completed_batches": 2,
            "total_batches": 2,
            "updated_files": 2,
        },
    ]


@pytest.mark.asyncio
async def test_project_index_delete_runner_applies_batches_and_reports_progress() -> None:
    store = RecordingDeleteBatchStore(
        results=[
            ProjectIndexDeleteBatchResult(
                deleted_entities=1,
                relation_cleanup_entity_ids=frozenset({99}),
                missing_paths=("notes/b.md",),
            ),
            ProjectIndexDeleteBatchResult(
                deleted_entities=0,
                missing_paths=("notes/c.md",),
            ),
        ]
    )
    metadata_reporter = RecordingProjectIndexMetadataReporter()

    run = await run_project_index_delete_batches(
        deleted_paths=("notes/a.md", "notes/b.md", "notes/c.md"),
        batch_size=2,
        delete_store=store,
        metadata_reporter=metadata_reporter,
    )

    assert store.batches == [
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/a.md", "notes/b.md"),
        ),
        ProjectIndexDeleteBatch(
            completed_batches=2,
            paths=("notes/c.md",),
        ),
    ]
    assert run == ProjectIndexDeleteRun(
        total_deletes=3,
        total_deleted_entities=1,
        relation_cleanup_entity_ids=frozenset({99}),
        records=run.records,
    )
    assert run.missing_paths == ("notes/b.md", "notes/c.md")
    assert metadata_reporter.progress_updates == [
        {
            "deleted_files": 3,
            "completed_batches": 1,
            "total_batches": 2,
            "deleted_entities": 1,
        },
    ]
    assert run.records[1].progress is None


@pytest.mark.asyncio
async def test_store_project_index_maintenance_runner_delegates_to_batch_stores() -> None:
    move_store = RecordingMoveBatchStore(results=[ProjectIndexMoveBatchResult(updated_files=1)])
    delete_store = RecordingDeleteBatchStore(
        results=[
            ProjectIndexDeleteBatchResult(
                deleted_entities=1,
                relation_cleanup_entity_ids=frozenset({99}),
            )
        ]
    )
    runner = StoreProjectIndexMaintenanceRunner(
        move_store=move_store,
        delete_store=delete_store,
    )

    move_run = await runner.run_move_batches(
        moved_files={"notes/a.md": "archive/a.md"},
        batch_size=50,
    )
    delete_run = await runner.run_delete_batches(
        deleted_paths=("notes/deleted.md",),
        batch_size=50,
    )

    assert move_run.total_updated_files == 1
    assert move_store.batches == [
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
        )
    ]
    assert delete_run.total_deleted_entities == 1
    assert delete_run.relation_cleanup_entity_ids == frozenset({99})
    assert delete_store.batches == [
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/deleted.md",),
        )
    ]


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_applies_move_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {"id": 10, "file_path": "notes/a.md", "permalink": "main/notes/a"},
                ]
            )
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_workflow_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
    )

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(
                ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),
                ProjectIndexMoveTarget("notes/b.md", "archive/b.md"),
            ),
        )
    )

    assert result == ProjectIndexMoveBatchResult(
        updated_files=1,
        moved_entity_ids=frozenset({10}),
        missing_paths=("notes/b.md",),
    )
    assert len(session.statements) == 5
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT entity.id, entity.file_path" in str(session.statements[1])
    assert "UPDATE entity" in str(session.statements[2])
    assert "UPDATE note_content" in str(session.statements[3])
    assert "UPDATE search_index" in str(session.statements[4])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_deletes_replaced_move_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {"id": 10, "file_path": "other/doc-1.pdf", "permalink": None},
                ]
            ),
            FakeProjectIndexResult(
                mapping_rows=[
                    {"id": 20, "file_path": "doc.pdf"},
                ]
            ),
            FakeProjectIndexResult(scalar_values=[99]),
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_workflow_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
    )

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("other/doc-1.pdf", "doc.pdf"),),
        )
    )

    assert result == ProjectIndexMoveBatchResult(
        updated_files=1,
        moved_entity_ids=frozenset({10}),
        replaced_entity_ids=frozenset({20}),
        relation_cleanup_entity_ids=frozenset({99}),
    )
    assert len(session.statements) == 9
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT entity.id, entity.file_path" in str(session.statements[1])
    assert "SELECT DISTINCT relation.from_id" in str(session.statements[2])
    assert "DELETE FROM search_index" in str(session.statements[3])
    assert "DELETE FROM search_vector_chunks" in str(session.statements[4])
    assert "DELETE FROM entity" in str(session.statements[5])
    assert "UPDATE entity" in str(session.statements[6])
    assert "UPDATE note_content" in str(session.statements[7])
    assert "UPDATE search_index" in str(session.statements[8])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_applies_move_content_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {"id": 10, "file_path": "notes/a.md", "permalink": "main/notes/a"},
                ]
            )
        ]
    )
    content_updater = RecordingMoveContentUpdater(
        updates={
            10: project_index_workflow_module.ProjectIndexMovedFileContentUpdate(
                permalink="main/archive/a",
                checksum="updated-checksum",
                markdown_content="---\npermalink: main/archive/a\n---\n\n# A\n",
            )
        }
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_workflow_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        move_content_updater=content_updater,
    )

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
        )
    )

    assert result == ProjectIndexMoveBatchResult(
        updated_files=1,
        moved_entity_ids=frozenset({10}),
    )
    assert content_updater.seen_files == [
        project_index_workflow_module.ProjectIndexMovedFile(
            entity_id=10,
            old_path="notes/a.md",
            new_path="archive/a.md",
            old_permalink="main/notes/a",
        )
    ]
    assert len(session.statements) == 6
    assert "checksum" in str(session.statements[2])
    assert "permalink" in str(session.statements[2])
    assert "markdown_content" in str(session.statements[3])
    assert "db_checksum" in str(session.statements[3])
    assert "file_checksum" in str(session.statements[3])
    assert "UPDATE search_index" in str(session.statements[4])
    assert "search_index.type" in str(session.statements[5])
    assert "permalink" in str(session.statements[5])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_applies_delete_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {"id": 10, "file_path": "notes/a.md"},
                    {"id": 20, "file_path": "notes/b.md"},
                ]
            ),
            FakeProjectIndexResult(scalar_values=[99]),
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_workflow_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
    )

    result = await store.apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/a.md", "notes/b.md", "notes/missing.md"),
        )
    )

    assert result == ProjectIndexDeleteBatchResult(
        deleted_entities=2,
        relation_cleanup_entity_ids=frozenset({99}),
        missing_paths=("notes/missing.md",),
    )
    assert len(session.statements) == 5
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT DISTINCT relation.from_id" in str(session.statements[1])
    assert "DELETE FROM search_index" in str(session.statements[2])
    assert "DELETE FROM search_vector_chunks" in str(session.statements[3])
    assert "DELETE FROM entity" in str(session.statements[4])


def test_project_index_workflow_start_builds_existing_metadata_and_attempt_event() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    start = build_project_index_workflow_start(
        request=request,
        total_files=4,
        batch_count=2,
        batch_size=50,
        discovered_at="2026-06-19T10:20:30+00:00",
        transport_broker="pgq",
        transport_entrypoint="index_project",
        transport_job_id=123,
    )

    assert start == ProjectIndexWorkflowStart(
        counters=ProjectIndexCounters(
            total=4,
            processed=0,
            succeeded=0,
            missing=0,
            failed=0,
        ),
        progress="Indexed 0/4 files, 0 succeeded",
        metadata={
            "phase": "indexing",
            "progress": "Indexed 0/4 files, 0 succeeded",
            "payload": {
                "tenant_id": str(tenant_id),
                "project_id": 42,
                "project_external_id": "external-project",
                "project_name": "Project Name",
                "project_permalink": "project-name",
                "project_path": "project",
                "force_full": True,
                "search": True,
                "embeddings": False,
            },
            "discovery": {
                "total_files": 4,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 4,
                "processed": 0,
                "succeeded": 0,
                "missing": 0,
                "failed": 0,
            },
            "transport": {
                "broker": "pgq",
                "entrypoint": "index_project",
                "pgq_job_id": "123",
            },
        },
        attempt_event_data={
            "phase": "indexing",
            "progress": "Indexed 0/4 files, 0 succeeded",
            "total_files": 4,
            "batch_count": 2,
            "batch_size": 50,
            "pgq_job_id": "123",
            "project_id": 42,
            "project_name": "Project Name",
            "project_permalink": "project-name",
            "project_path": "project",
        },
    )


def test_project_index_workflow_start_plan_keeps_nonempty_workflows_running() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    plan = plan_project_index_workflow_start(
        request=request,
        total_files=4,
        batch_count=2,
        batch_size=50,
        discovered_at="2026-06-19T10:20:30+00:00",
        transport_broker="pgq",
        transport_entrypoint="index_project",
        transport_job_id=123,
    )

    assert plan.status == "running"
    assert plan.is_complete is False
    assert plan.completion_update is None
    assert plan.workflow_start.progress == "Indexed 0/4 files, 0 succeeded"
    assert plan.workflow_start.metadata["phase"] == "indexing"
    assert plan.workflow_start.metadata["transport"] == {
        "broker": "pgq",
        "entrypoint": "index_project",
        "pgq_job_id": "123",
    }
    with pytest.raises(RuntimeError, match="does not include a completion update"):
        plan.require_completion_update()


def test_project_index_workflow_start_plan_completes_empty_projects() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    request = ProjectIndexWorkflowRequest.from_source(
        ProjectIndexSource(
            tenant_id=tenant_id,
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            workflow_id=workflow_id,
            force_full=False,
            search=True,
            embeddings=False,
        )
    )

    plan = plan_project_index_workflow_start(
        request=request,
        total_files=0,
        batch_count=0,
        batch_size=50,
        discovered_at="2026-06-19T10:20:30+00:00",
        transport_broker="pgq",
        transport_entrypoint="index_project",
        transport_job_id=None,
    )

    assert plan == ProjectIndexWorkflowStartPlan.complete(
        workflow_start=ProjectIndexWorkflowStart(
            counters=ProjectIndexCounters(
                total=0,
                processed=0,
                succeeded=0,
                missing=0,
                failed=0,
            ),
            progress="No files found",
            metadata={
                "phase": "indexing",
                "progress": "No files found",
                "payload": {
                    "tenant_id": str(tenant_id),
                    "project_id": 42,
                    "project_external_id": "external-project",
                    "project_name": "Project Name",
                    "project_permalink": "project-name",
                    "project_path": "project",
                    "force_full": False,
                    "search": True,
                    "embeddings": False,
                },
                "discovery": {
                    "total_files": 0,
                    "batch_count": 0,
                    "batch_size": 50,
                    "discovered_at": "2026-06-19T10:20:30+00:00",
                },
                "counters": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
                "transport": {
                    "broker": "pgq",
                    "entrypoint": "index_project",
                    "pgq_job_id": None,
                },
            },
            attempt_event_data={
                "phase": "indexing",
                "progress": "No files found",
                "total_files": 0,
                "batch_count": 0,
                "batch_size": 50,
                "pgq_job_id": None,
                "project_id": 42,
                "project_name": "Project Name",
                "project_permalink": "project-name",
                "project_path": "project",
            },
        ),
        completion_update=ProjectIndexWorkflowCompletionUpdate(
            counters=ProjectIndexCounters(
                total=0,
                processed=0,
                succeeded=0,
                missing=0,
                failed=0,
            ),
            progress="No files found",
            metadata={
                "phase": "completed",
                "progress": "No files found",
                "payload": {
                    "tenant_id": str(tenant_id),
                    "project_id": 42,
                    "project_external_id": "external-project",
                    "project_name": "Project Name",
                    "project_permalink": "project-name",
                    "project_path": "project",
                    "force_full": False,
                    "search": True,
                    "embeddings": False,
                },
                "discovery": {
                    "total_files": 0,
                    "batch_count": 0,
                    "batch_size": 50,
                    "discovered_at": "2026-06-19T10:20:30+00:00",
                },
                "counters": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
                "transport": {
                    "broker": "pgq",
                    "entrypoint": "index_project",
                    "pgq_job_id": None,
                },
                "result": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
            },
            completed_event_data={
                "phase": "completed",
                "progress": "No files found",
                "payload": {
                    "tenant_id": str(tenant_id),
                    "project_id": 42,
                    "project_external_id": "external-project",
                    "project_name": "Project Name",
                    "project_permalink": "project-name",
                    "project_path": "project",
                    "force_full": False,
                    "search": True,
                    "embeddings": False,
                },
                "result": {
                    "total": 0,
                    "processed": 0,
                    "succeeded": 0,
                    "missing": 0,
                    "failed": 0,
                },
            },
        ),
    )
    assert plan.is_complete is True
    assert plan.require_completion_update().metadata["phase"] == "completed"


def test_project_index_workflow_progress_update_builds_metadata_and_event_data() -> None:
    counters = ProjectIndexCounters(
        total=100,
        processed=50,
        succeeded=49,
        missing=1,
        failed=0,
    )

    update = build_project_index_workflow_progress_update(
        metadata={
            "phase": "indexing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 0,
                "succeeded": 0,
                "missing": 0,
                "failed": 0,
            },
        },
        counters=counters,
        recorded_batch_indexes=(0,),
    )

    assert update == ProjectIndexWorkflowProgressUpdate(
        counters=counters,
        progress="Indexed 50/100 files, 49 succeeded, 1 missing",
        should_emit_event=True,
        metadata={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
        },
        progress_event_data={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
        },
    )


def test_project_index_workflow_completion_update_builds_metadata_and_event_data() -> None:
    counters = ProjectIndexCounters(
        total=100,
        processed=100,
        succeeded=99,
        missing=1,
        failed=0,
    )

    update = build_project_index_workflow_completion_update(
        metadata={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0, 1],
        },
        counters=counters,
        progress="Indexed 100/100 files, 99 succeeded, 1 missing",
    )

    assert update == ProjectIndexWorkflowCompletionUpdate(
        counters=counters,
        progress="Indexed 100/100 files, 99 succeeded, 1 missing",
        metadata={
            "phase": "completed",
            "progress": "Indexed 100/100 files, 99 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 100,
                "succeeded": 99,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0, 1],
            "result": {
                "total": 100,
                "processed": 100,
                "succeeded": 99,
                "missing": 1,
                "failed": 0,
            },
        },
        completed_event_data={
            "phase": "completed",
            "progress": "Indexed 100/100 files, 99 succeeded, 1 missing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "result": {
                "total": 100,
                "processed": 100,
                "succeeded": 99,
                "missing": 1,
                "failed": 0,
            },
        },
    )


def test_project_index_file_result_record_plan_builds_progress_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_file_result_record(
        metadata=project_index_record_metadata(total=2),
        workflow_id=workflow_id,
        result=IndexFileJobResult(
            status=IndexFileJobStatus.processed,
            reason="file indexed: notes/a.md",
        ),
    )

    assert plan.status == "progress"
    assert plan.is_complete is False
    assert plan.should_emit_progress_event is True
    assert plan.completion_update is None
    progress_update = plan.require_progress_update()
    assert progress_update.counters == ProjectIndexCounters(
        total=2,
        processed=1,
        succeeded=1,
        missing=0,
        failed=0,
    )
    assert progress_update.metadata["phase"] == "indexing"
    assert progress_update.metadata["progress"] == "Indexed 1/2 files, 1 succeeded"
    assert progress_update.progress_event_data == {
        "phase": "indexing",
        "progress": "Indexed 1/2 files, 1 succeeded",
        "payload": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "project_id": 42,
            "project_external_id": "external-project",
        },
        "counters": {
            "total": 2,
            "processed": 1,
            "succeeded": 1,
            "missing": 0,
            "failed": 0,
        },
    }


def test_project_index_file_result_record_plan_builds_completion_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_file_result_record(
        metadata=project_index_record_metadata(total=1),
        workflow_id=workflow_id,
        result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason="file current: notes/a.md",
        ),
    )

    assert plan.status == "complete"
    assert plan.is_complete is True
    progress_update = plan.require_progress_update()
    completion_update = plan.require_completion_update()
    assert progress_update.metadata["phase"] == "indexing"
    assert completion_update.counters == ProjectIndexCounters(
        total=1,
        processed=1,
        succeeded=1,
        missing=0,
        failed=0,
    )
    assert completion_update.metadata["phase"] == "completed"
    assert completion_update.metadata["result"] == {
        "total": 1,
        "processed": 1,
        "succeeded": 1,
        "missing": 0,
        "failed": 0,
    }
    assert completion_update.completed_event_data == {
        "phase": "completed",
        "progress": "Indexed 1/1 files, 1 succeeded",
        "payload": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "project_id": 42,
            "project_external_id": "external-project",
        },
        "result": {
            "total": 1,
            "processed": 1,
            "succeeded": 1,
            "missing": 0,
            "failed": 0,
        },
    }


def test_project_index_batch_result_record_plan_ignores_recorded_batches() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_batch_result_record(
        metadata=project_index_record_metadata(
            total=2,
            processed=1,
            succeeded=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        batch_index=0,
        batch_count=2,
        results=[
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/a.md",
            )
        ],
    )

    assert plan == ProjectIndexWorkflowRecordPlan.already_recorded()
    assert plan.should_emit_progress_event is False
    with pytest.raises(RuntimeError, match="does not include a progress update"):
        plan.require_progress_update()


def test_project_index_batch_result_record_plan_builds_completion_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_batch_result_record(
        metadata=project_index_record_metadata(
            total=2,
            processed=1,
            succeeded=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        batch_index=1,
        batch_count=2,
        results=[
            IndexFileJobResult(
                status=IndexFileJobStatus.missing,
                reason="file missing: notes/b.md",
            )
        ],
    )

    assert plan.status == "complete"
    assert plan.is_complete is True
    progress_update = plan.require_progress_update()
    completion_update = plan.require_completion_update()
    assert progress_update.metadata["recorded_batches"] == [0, 1]
    assert completion_update.metadata["phase"] == "completed"
    assert completion_update.metadata["recorded_batches"] == [0, 1]
    assert completion_update.metadata["result"] == {
        "total": 2,
        "processed": 2,
        "succeeded": 1,
        "missing": 1,
        "failed": 0,
    }


def test_project_index_stale_workflow_plan_keeps_active_batches_running() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    active_batch_jobs = ProjectIndexBatchJobActivity(
        batch_indexes=(1,),
        queued_count=1,
        picked_fresh_count=0,
        picked_stale_count=0,
    )

    plan = plan_project_index_stale_workflow(
        metadata=project_index_record_metadata(
            total=100,
            processed=50,
            succeeded=49,
            missing=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        active_batch_jobs=active_batch_jobs,
        observed_at="2026-06-19T10:24:00+00:00",
        last_heartbeat_at="2026-06-19T10:20:30+00:00",
        stale_before="2026-06-19T10:25:30+00:00",
    )

    assert plan.status == "keep_running"
    assert plan.should_fail is False
    assert plan.failure_update is None
    assert plan.require_activity_update() == ProjectIndexBatchJobActivityUpdate(
        activity=active_batch_jobs,
        metadata={
            "phase": "indexing",
            "progress": "Indexed 50/100 files, 49 succeeded",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "discovery": {
                "total_files": 100,
                "batch_count": 2,
                "batch_size": 50,
                "discovered_at": "2026-06-19T10:20:30+00:00",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
            "last_batch_job_activity": {
                "active_batches": [1],
                "queued_count": 1,
                "picked_fresh_count": 0,
                "picked_stale_count": 0,
                "observed_at": "2026-06-19T10:24:00+00:00",
            },
        },
    )
    with pytest.raises(RuntimeError, match="does not include a failure update"):
        plan.require_failure_update()


def test_project_index_stale_workflow_plan_builds_failure_update() -> None:
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")

    plan = plan_project_index_stale_workflow(
        metadata=project_index_record_metadata(
            total=100,
            processed=50,
            succeeded=49,
            missing=1,
            recorded_batches=[0],
        ),
        workflow_id=workflow_id,
        active_batch_jobs=ProjectIndexBatchJobActivity.empty(),
        observed_at="2026-06-19T10:24:00+00:00",
        last_heartbeat_at="2026-06-19T10:20:30+00:00",
        stale_before="2026-06-19T10:25:30+00:00",
    )

    diagnostics = {
        "reason": "stale_project_index_batches",
        "missing_batches": [1],
        "recorded_batches": [0],
        "legacy_missing_batch_count": 0,
        "last_heartbeat_at": "2026-06-19T10:20:30+00:00",
        "stale_before": "2026-06-19T10:25:30+00:00",
    }
    assert plan == ProjectIndexStaleWorkflowPlan.fail(
        ProjectIndexWorkflowFailureUpdate(
            counters=ProjectIndexCounters(
                total=100,
                processed=50,
                succeeded=49,
                missing=1,
                failed=0,
            ),
            progress="Project index stalled after 50/100 files",
            error_message="Project index stalled with 1 unreported batch(es)",
            metadata={
                "phase": "failed",
                "progress": "Project index stalled after 50/100 files",
                "payload": {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "project_id": 42,
                    "project_external_id": "external-project",
                },
                "discovery": {
                    "total_files": 100,
                    "batch_count": 2,
                    "batch_size": 50,
                    "discovered_at": "2026-06-19T10:20:30+00:00",
                },
                "counters": {
                    "total": 100,
                    "processed": 50,
                    "succeeded": 49,
                    "missing": 1,
                    "failed": 0,
                },
                "recorded_batches": [0],
                "diagnostics": diagnostics,
            },
            failed_event_data={
                "phase": "failed",
                "progress": "Project index stalled after 50/100 files",
                "payload": {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "project_id": 42,
                    "project_external_id": "external-project",
                },
                "error": "Project index stalled with 1 unreported batch(es)",
                "diagnostics": diagnostics,
            },
        )
    )
    assert plan.should_fail is True
    with pytest.raises(RuntimeError, match="does not include an activity update"):
        plan.require_activity_update()


def test_project_index_workflow_stale_failure_update_builds_metadata_and_event_data() -> None:
    counters = ProjectIndexCounters(
        total=100,
        processed=50,
        succeeded=49,
        missing=1,
        failed=0,
    )

    update = build_project_index_workflow_stale_failure_update(
        metadata={
            "phase": "indexing",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
        },
        counters=counters,
        missing_batch_indexes=(1,),
        recorded_batch_indexes=(0,),
        legacy_missing_batch_count=0,
        last_heartbeat_at="2026-06-19T10:20:30+00:00",
        stale_before="2026-06-19T10:25:30+00:00",
    )

    diagnostics = {
        "reason": "stale_project_index_batches",
        "missing_batches": [1],
        "recorded_batches": [0],
        "legacy_missing_batch_count": 0,
        "last_heartbeat_at": "2026-06-19T10:20:30+00:00",
        "stale_before": "2026-06-19T10:25:30+00:00",
    }
    assert update == ProjectIndexWorkflowFailureUpdate(
        counters=counters,
        progress="Project index stalled after 50/100 files",
        error_message="Project index stalled with 1 unreported batch(es)",
        metadata={
            "phase": "failed",
            "progress": "Project index stalled after 50/100 files",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "counters": {
                "total": 100,
                "processed": 50,
                "succeeded": 49,
                "missing": 1,
                "failed": 0,
            },
            "recorded_batches": [0],
            "diagnostics": diagnostics,
        },
        failed_event_data={
            "phase": "failed",
            "progress": "Project index stalled after 50/100 files",
            "payload": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "project_id": 42,
                "project_external_id": "external-project",
            },
            "error": "Project index stalled with 1 unreported batch(es)",
            "diagnostics": diagnostics,
        },
    )
