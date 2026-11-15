"""Tests for project-index move/delete maintenance."""

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, override, cast

import basic_memory.indexing.project_index_maintenance as project_index_maintenance_module
import basic_memory.repository.accepted_note_vector_cleanup as accepted_note_vector_cleanup_module
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.project_index_maintenance import (
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
    RepositoryProjectIndexMaintenanceStore,
    RepositoryProjectIndexMovedEntitySearchRefresher,
    StoreProjectIndexMaintenanceRunner,
    build_project_index_delete_batch_plan,
    build_project_index_move_batch_plan,
    run_project_index_delete_batches,
    run_project_index_move_batches,
)
from basic_memory.indexing.note_content_reconciler import NoteContentReconciler
from basic_memory.indexing.wiki_projector import (
    WIKI_PROJECTOR_VERSION,
    WikiChangeOperation,
    WikiProjectionReason,
    WikiProjectionRequest,
    WikiProjectionSnapshot,
    WikiSourceChange,
    plan_wiki_projection,
)
from basic_memory.runtime.project_partition import ACCEPTED_STORAGE_CHANGE_SOURCE
from basic_memory.models import AcceptedProjectNoteChange, Entity, NoteContent, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository


def _stub_move_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[ProjectIndexMoveBatchResult],
    recorded: list[ProjectIndexMoveBatch],
) -> RepositoryProjectIndexMaintenanceStore:
    """Build a real store whose move-batch apply returns canned results.

    The orchestration under test only cares that each batch produces a result and
    is recorded in order, so we monkeypatch the single SQL method rather than drive
    a real database — the store is otherwise the same concrete used in production.
    """
    result_iter = iter(results)

    async def fake_apply_move_batch(
        _self: RepositoryProjectIndexMaintenanceStore,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        recorded.append(move_batch)
        return next(result_iter)

    monkeypatch.setattr(
        RepositoryProjectIndexMaintenanceStore,
        "apply_project_index_move_batch",
        fake_apply_move_batch,
    )
    return RepositoryProjectIndexMaintenanceStore(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        project_id=1,
    )


def _stub_delete_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[ProjectIndexDeleteBatchResult],
    recorded: list[ProjectIndexDeleteBatch],
) -> RepositoryProjectIndexMaintenanceStore:
    """Build a real store whose delete-batch apply returns canned results."""
    result_iter = iter(results)

    async def fake_apply_delete_batch(
        _self: RepositoryProjectIndexMaintenanceStore,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        recorded.append(delete_batch)
        return next(result_iter)

    monkeypatch.setattr(
        RepositoryProjectIndexMaintenanceStore,
        "apply_project_index_delete_batch",
        fake_apply_delete_batch,
    )
    return RepositoryProjectIndexMaintenanceStore(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        project_id=1,
    )


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


def storage_owned_delete_candidate_row(entity_id: int, file_path: str) -> dict[str, object]:
    """Return the persistence projection for one storage-owned delete candidate."""
    return {
        "entity_id": entity_id,
        "file_path": file_path,
        "note_content_entity_id": None,
        "db_version": None,
        "db_checksum": None,
        "file_version": None,
        "file_checksum": None,
        "file_write_status": None,
        "last_materialization_attempt_at": None,
    }


def materialized_delete_candidate_row(
    entity_id: int,
    file_path: str,
    *,
    file_write_status: str = "synced",
    db_version: int = 3,
    db_checksum: str = "current-checksum",
    file_version: int | None = 3,
    file_checksum: str | None = "current-checksum",
    last_materialization_attempt_at: object = None,
) -> dict[str, object]:
    """Return the persistence projection for one accepted note delete candidate."""
    return {
        "entity_id": entity_id,
        "file_path": file_path,
        "note_content_entity_id": entity_id,
        "db_version": db_version,
        "db_checksum": db_checksum,
        "file_version": file_version,
        "file_checksum": file_checksum,
        "file_write_status": file_write_status,
        "last_materialization_attempt_at": last_materialization_attempt_at,
    }


def test_project_index_delete_candidate_constructs_fully_materialized_note() -> None:
    attempted_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

    candidate = project_index_maintenance_module._project_index_delete_candidate_from_row(
        materialized_delete_candidate_row(
            10,
            "notes/a.md",
            last_materialization_attempt_at=attempted_at,
        )
    )

    assert (
        candidate
        == project_index_maintenance_module.MaterializedNoteProjectIndexDeleteCandidate(
            entity=project_index_maintenance_module.ProjectIndexDeleteEntity(
                entity_id=10,
                file_path="notes/a.md",
            ),
            db_version=3,
            db_checksum="current-checksum",
            file_version=3,
            file_checksum="current-checksum",
            last_materialization_attempt_at=attempted_at,
        )
    )


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"file_write_status": "pending"},
        {"file_write_status": "writing"},
        {"file_write_status": "failed"},
        {"file_write_status": "external_change_detected"},
        {"file_version": 2},
        {"file_checksum": "older-checksum"},
    ],
)
def test_project_index_delete_candidate_rejects_unsynchronized_note_state(
    overrides: dict[str, object],
) -> None:
    row = materialized_delete_candidate_row(10, "notes/a.md")
    row.update(overrides)

    candidate = project_index_maintenance_module._project_index_delete_candidate_from_row(row)

    assert candidate is None


def test_project_index_delete_candidate_rejects_invalid_attempt_timestamp() -> None:
    row = materialized_delete_candidate_row(
        10,
        "notes/a.md",
        last_materialization_attempt_at="not-a-datetime",
    )

    with pytest.raises(TypeError, match="last_materialization_attempt_at must be a datetime"):
        project_index_maintenance_module._project_index_delete_candidate_from_row(row)


@dataclass(slots=True)
class FakeProjectIndexSession:
    """Record repository maintenance statements without a real database."""

    results: list[FakeProjectIndexResult]
    dialect_name: str = "sqlite"
    statements: list[object] = field(default_factory=list)
    params: list[object | None] = field(default_factory=list)

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect_name))

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
    """Record moved-file plan/write requests and return configured content updates."""

    updates: dict[int, project_index_maintenance_module.ProjectIndexMovedFileContentUpdate]
    seen_files: list[project_index_maintenance_module.ProjectIndexMovedFile] = field(
        default_factory=list
    )
    written: list[
        tuple[
            project_index_maintenance_module.ProjectIndexMovedFile,
            project_index_maintenance_module.ProjectIndexMovedFileContentUpdate,
        ]
    ] = field(default_factory=list)
    events: list[str] | None = None

    async def plan_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: project_index_maintenance_module.ProjectIndexMovedFile,
    ) -> project_index_maintenance_module.ProjectIndexMovedFileContentUpdate | None:
        del session
        self.seen_files.append(moved_file)
        return self.updates.get(moved_file.entity_id)

    fail_writes: bool = False
    # Checksum a write reports as stored, by entity id; the planned checksum otherwise.
    stored_checksums: dict[int, str] = field(default_factory=dict)

    async def write_moved_file_content(
        self,
        moved_file: project_index_maintenance_module.ProjectIndexMovedFile,
        content_update: project_index_maintenance_module.ProjectIndexMovedFileContentUpdate,
    ) -> str:
        if self.fail_writes:
            raise RuntimeError("simulated write failure")
        if self.events is not None:
            self.events.append("write")
        self.written.append((moved_file, content_update))
        return self.stored_checksums.get(moved_file.entity_id, content_update.checksum)


@dataclass(slots=True)
class StaticMovedEntityRepository:
    """Return only the moved entities that still have a database row."""

    entities: list[Entity]

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[Entity]:
        del session
        return [entity for entity in self.entities if entity.id in ids]


@dataclass(slots=True)
class RecordingMovedEntityIndexer:
    """Record which moved entities were search-refreshed."""

    indexed_entity_ids: list[int] = field(default_factory=list)

    async def index_entity(self, entity: Entity) -> object:
        self.indexed_entity_ids.append(entity.id)
        return entity


@pytest.mark.asyncio
async def test_moved_entity_search_refresher_skips_entities_deleted_mid_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moved entity deleted between move commit and refresh must not abort the run."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(results=[])

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    surviving_entity = cast(Entity, SimpleNamespace(id=10))
    entity_indexer = RecordingMovedEntityIndexer()
    refresher = RepositoryProjectIndexMovedEntitySearchRefresher(
        session_maker=session_maker,
        entity_repository=StaticMovedEntityRepository(entities=[surviving_entity]),
        entity_indexer=entity_indexer,
    )

    # Entity 5 was deleted concurrently; only entity 10 can still be refreshed.
    await refresher.refresh_moved_entities([10, 5, 10])

    assert entity_indexer.indexed_entity_ids == [10]


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
async def test_project_index_move_runner_applies_batches_and_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_batches: list[ProjectIndexMoveBatch] = []
    store = _stub_move_store(
        monkeypatch,
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
        ],
        recorded=recorded_batches,
    )

    run = await run_project_index_move_batches(
        moved_files={
            "notes/a.md": "archive/a.md",
            "notes/b.md": "archive/b.md",
            "notes/c.md": "archive/c.md",
        },
        batch_size=2,
        move_store=store,
    )

    assert recorded_batches == [
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


@pytest.mark.asyncio
async def test_project_index_delete_runner_applies_batches_and_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_batches: list[ProjectIndexDeleteBatch] = []
    store = _stub_delete_store(
        monkeypatch,
        results=[
            ProjectIndexDeleteBatchResult(
                deleted_entities=1,
                relation_cleanup_entity_ids=frozenset({99}),
                missing_paths=("notes/b.md",),
            ),
            ProjectIndexDeleteBatchResult(
                deleted_entities=0,
                missing_paths=("notes/c.md",),
                skipped_paths=("notes/d.md",),
            ),
        ],
        recorded=recorded_batches,
    )

    run = await run_project_index_delete_batches(
        deleted_paths=("notes/a.md", "notes/b.md", "notes/c.md"),
        batch_size=2,
        delete_store=store,
    )

    assert recorded_batches == [
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
    assert run.skipped_paths == ("notes/d.md",)
    assert run.records[1].progress is None


@pytest.mark.asyncio
async def test_store_project_index_maintenance_runner_delegates_to_batch_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_move_batches: list[ProjectIndexMoveBatch] = []
    recorded_delete_batches: list[ProjectIndexDeleteBatch] = []
    move_store = _stub_move_store(
        monkeypatch,
        results=[ProjectIndexMoveBatchResult(updated_files=1)],
        recorded=recorded_move_batches,
    )
    delete_store = _stub_delete_store(
        monkeypatch,
        results=[
            ProjectIndexDeleteBatchResult(
                deleted_entities=1,
                relation_cleanup_entity_ids=frozenset({99}),
            )
        ],
        recorded=recorded_delete_batches,
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
    assert recorded_move_batches == [
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
        )
    ]
    assert delete_run.total_deleted_entities == 1
    assert delete_run.relation_cleanup_entity_ids == frozenset({99})
    assert recorded_delete_batches == [
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
        project_index_maintenance_module.db,
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
    assert len(session.statements) == 7
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT entity.id, entity.file_path" in str(session.statements[1])
    assert "ORDER BY note_content.entity_id" in str(session.statements[2])
    assert "UPDATE entity" in str(session.statements[3])
    assert "UPDATE note_content" in str(session.statements[4])
    assert "UPDATE search_index" in str(session.statements[5])
    # The moved note's journal identity is read after the path updates. This fake
    # returns no rows, so nothing is journaled and no position is claimed.
    assert "SELECT entity.id, entity.external_id" in str(session.statements[6])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_move_batch_handles_empty_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch with no targets returns before touching the database, and a batch
    whose paths match no indexed rows reports them missing without deleting or
    updating anything."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(results=[FakeProjectIndexResult()])

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
    )

    empty_result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(completed_batches=1, targets=())
    )
    assert empty_result == ProjectIndexMoveBatchResult(updated_files=0)
    assert session.statements == []

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/gone.md", "archive/gone.md"),),
        )
    )
    assert result == ProjectIndexMoveBatchResult(
        updated_files=0,
        missing_paths=("notes/gone.md",),
    )
    # Only the target-row select ran: no replacement lookup, deletes, or updates.
    assert len(session.statements) == 1


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
            FakeProjectIndexResult(),  # complete move-set NoteContent fence
            FakeProjectIndexResult(),  # replaced entity's journal identity
            FakeProjectIndexResult(),  # replacement-delete NoteContent fence
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
        project_index_maintenance_module.db,
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
    assert len(session.statements) == 13
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT entity.id, entity.file_path" in str(session.statements[1])
    assert "ORDER BY note_content.entity_id" in str(session.statements[2])
    # The replaced entity's journal identity is read before its row is deleted.
    assert "SELECT entity.id, entity.external_id" in str(session.statements[3])
    assert "ORDER BY note_content.entity_id" in str(session.statements[4])
    assert "SELECT DISTINCT relation.from_id" in str(session.statements[5])
    assert "DELETE FROM search_index" in str(session.statements[6])
    assert "sqlite_master" in str(session.statements[7])
    assert "DELETE FROM entity" in str(session.statements[8])
    assert "UPDATE entity" in str(session.statements[9])
    assert "UPDATE note_content" in str(session.statements[10])
    assert "UPDATE search_index" in str(session.statements[11])
    assert "SELECT entity.id, entity.external_id" in str(session.statements[12])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_drops_move_onto_concurrent_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified move must not delete a concurrently created destination entity."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {
                        "id": 10,
                        "file_path": "notes/a.md",
                        "permalink": None,
                        "checksum": "moved-checksum",
                    },
                ]
            ),
            FakeProjectIndexResult(
                mapping_rows=[
                    # Created after the scan snapshot with different content —
                    # e.g. an accepted write_note that is not yet materialized.
                    {"id": 20, "file_path": "archive/a.md", "checksum": "accepted-checksum"},
                ]
            ),
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        verify_replaced_move_targets=True,
    )

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
        )
    )

    assert result == ProjectIndexMoveBatchResult(
        updated_files=0,
        dropped_move_paths=("notes/a.md",),
    )
    # Only the two SELECTs ran: nothing was deleted or repointed.
    assert len(session.statements) == 2
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT entity.id, entity.file_path" in str(session.statements[1])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_replaces_destination_with_moved_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified move still dedupes a destination row that indexes the moved bytes."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {
                        "id": 10,
                        "file_path": "notes/a.md",
                        "permalink": None,
                        "checksum": "moved-checksum",
                    },
                ]
            ),
            FakeProjectIndexResult(
                mapping_rows=[
                    # A racing event index already created a row for the moved
                    # file at its new path: same bytes, safe to dedupe.
                    {"id": 20, "file_path": "archive/a.md", "checksum": "moved-checksum"},
                ]
            ),
            FakeProjectIndexResult(),  # complete move-set NoteContent fence
            FakeProjectIndexResult(),  # replaced entity's journal identity
            FakeProjectIndexResult(),  # replacement-delete NoteContent fence
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
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        verify_replaced_move_targets=True,
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
        replaced_entity_ids=frozenset({20}),
        relation_cleanup_entity_ids=frozenset({99}),
    )
    assert any("DELETE FROM entity" in str(statement) for statement in session.statements)


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
            10: project_index_maintenance_module.ProjectIndexMovedFileContentUpdate(
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
        project_index_maintenance_module.db,
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
        project_index_maintenance_module.ProjectIndexMovedFile(
            entity_id=10,
            old_path="notes/a.md",
            new_path="archive/a.md",
            old_permalink="main/notes/a",
        )
    ]
    assert content_updater.written == [(content_updater.seen_files[0], content_updater.updates[10])]
    assert len(session.statements) == 8
    assert "ORDER BY note_content.entity_id" in str(session.statements[2])
    assert "checksum" in str(session.statements[3])
    assert "permalink" in str(session.statements[3])
    assert "markdown_content" in str(session.statements[4])
    assert "db_checksum" in str(session.statements[4])
    assert "file_checksum" in str(session.statements[4])
    assert "UPDATE search_index" in str(session.statements[5])
    assert "search_index.type" in str(session.statements[6])
    assert "permalink" in str(session.statements[6])
    assert "SELECT entity.id, entity.external_id" in str(session.statements[7])


@dataclass(frozen=True, slots=True)
class StaticDeletePathVerifier:
    """Confirm only the configured paths as still absent from storage."""

    confirmed: frozenset[str]

    async def confirm_deleted_paths(self, paths: Sequence[str]) -> frozenset[str]:
        return frozenset(path for path in paths if path in self.confirmed)


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_requires_live_delete_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[storage_owned_delete_candidate_row(10, "notes/a.md")]
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
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
    )

    with pytest.raises(
        RuntimeError,
        match="project-index delete requires a live storage path verifier",
    ):
        await store.apply_project_index_delete_batch(
            ProjectIndexDeleteBatch(completed_batches=1, paths=("notes/a.md",))
        )


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_protects_pending_note_without_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    materialized_delete_candidate_row(
                        10,
                        "notes/pending.md",
                        file_write_status="pending",
                        file_version=None,
                        file_checksum=None,
                    )
                ]
            )
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    result = await RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
    ).apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(completed_batches=1, paths=("notes/pending.md",))
    )

    assert result == ProjectIndexDeleteBatchResult(
        deleted_entities=0,
        skipped_paths=("notes/pending.md",),
    )


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_skips_deletes_for_reappeared_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path present in storage again at apply time must survive the delete batch."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    storage_owned_delete_candidate_row(10, "notes/a.md"),
                    storage_owned_delete_candidate_row(20, "notes/reappeared.md"),
                ]
            ),
            FakeProjectIndexResult(),  # sorted NoteContent lock fence
            FakeProjectIndexResult(),  # sorted Entity lock fence
            FakeProjectIndexResult(
                mapping_rows=[storage_owned_delete_candidate_row(10, "notes/a.md")]
            ),
            FakeProjectIndexResult(),  # delete helper NoteContent fence
            FakeProjectIndexResult(scalar_values=[]),
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        delete_path_verifier=StaticDeletePathVerifier(confirmed=frozenset({"notes/a.md"})),
    )

    result = await store.apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/a.md", "notes/reappeared.md"),
        )
    )

    assert result == ProjectIndexDeleteBatchResult(
        deleted_entities=1,
        skipped_paths=("notes/reappeared.md",),
    )
    # Only the confirmed path was queried and deleted.
    assert any("DELETE FROM entity" in str(statement) for statement in session.statements)


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_skips_whole_batch_when_nothing_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no planned delete is re-confirmed absent, the database is not touched."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    storage_owned_delete_candidate_row(10, "notes/a.md"),
                    storage_owned_delete_candidate_row(20, "notes/b.md"),
                ]
            )
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        delete_path_verifier=StaticDeletePathVerifier(confirmed=frozenset()),
    )

    result = await store.apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/a.md", "notes/b.md"),
        )
    )

    assert result == ProjectIndexDeleteBatchResult(
        deleted_entities=0,
        skipped_paths=("notes/a.md", "notes/b.md"),
    )
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_writes_moved_content_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moved-file frontmatter rewrites must land only after the batch commits."""
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
    events: list[str] = []

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session
        events.append("commit")

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    content_updater = RecordingMoveContentUpdater(
        updates={
            10: project_index_maintenance_module.ProjectIndexMovedFileContentUpdate(
                permalink="main/archive/a",
                checksum="updated-checksum",
                markdown_content="---\npermalink: main/archive/a\n---\n\n# A\n",
            )
        },
        events=events,
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        move_content_updater=content_updater,
    )

    await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
        )
    )

    assert events == ["commit", "write"]


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_logs_failed_post_commit_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-commit file-write failure is logged; the committed batch still succeeds."""
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
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    content_updater = RecordingMoveContentUpdater(
        updates={
            10: project_index_maintenance_module.ProjectIndexMovedFileContentUpdate(
                permalink="main/archive/a",
                checksum="updated-checksum",
                markdown_content="---\npermalink: main/archive/a\n---\n\n# A\n",
            )
        },
        fail_writes=True,
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

    # The next scan reconciles the stale file as modified (checksum mismatch).
    assert result.updated_files == 1
    assert content_updater.written == []


@dataclass(slots=True)
class FailingUpdateProjectIndexSession(FakeProjectIndexSession):
    """Fail the batch on its first UPDATE, simulating an intra-batch rollback."""

    @override
    async def execute(
        self,
        statement: object,
        params: object | None = None,
    ) -> FakeProjectIndexResult:
        if "UPDATE entity" in str(statement):
            raise RuntimeError("simulated intra-batch failure")
        # Constraint: `dataclass(slots=True)` cannot add __slots__ in place, so it
        # rebuilds the class. Before CPython 3.14 (gh-90562) the rebuilt methods keep
        # a `__class__` cell pointing at the discarded original, and zero-argument
        # `super()` then rejects `self` with "obj must be an instance or subtype of
        # type". Naming the class re-looks it up at call time, which resolves to the
        # rebuilt class on every supported interpreter.
        return await super(FailingUpdateProjectIndexSession, self).execute(statement, params)


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_failed_move_batch_writes_no_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A move batch that rolls back must leave every file untouched."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FailingUpdateProjectIndexSession(
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
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    content_updater = RecordingMoveContentUpdater(
        updates={
            10: project_index_maintenance_module.ProjectIndexMovedFileContentUpdate(
                permalink="main/archive/a",
                checksum="updated-checksum",
                markdown_content="---\npermalink: main/archive/a\n---\n\n# A\n",
            )
        }
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        move_content_updater=content_updater,
    )

    with pytest.raises(RuntimeError, match="simulated intra-batch failure"):
        await store.apply_project_index_move_batch(
            ProjectIndexMoveBatch(
                completed_batches=1,
                targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
            )
        )

    # The plan ran, but no file was rewritten for the rolled-back batch.
    assert len(content_updater.seen_files) == 1
    assert content_updater.written == []


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_applies_delete_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    storage_owned_delete_candidate_row(10, "notes/a.md"),
                    storage_owned_delete_candidate_row(20, "notes/b.md"),
                ]
            ),
            FakeProjectIndexResult(),  # sorted NoteContent lock fence
            FakeProjectIndexResult(),  # sorted Entity lock fence
            FakeProjectIndexResult(
                mapping_rows=[
                    storage_owned_delete_candidate_row(10, "notes/a.md"),
                    storage_owned_delete_candidate_row(20, "notes/b.md"),
                ]
            ),
            FakeProjectIndexResult(),  # deleted entities' journal identities
            FakeProjectIndexResult(),  # delete helper NoteContent fence
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
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        delete_path_verifier=project_index_maintenance_module.TrustPlannedProjectIndexDeleteVerifier(),
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
    assert len(session.statements) == 10
    assert "LEFT OUTER JOIN note_content" in str(session.statements[0])
    assert "ORDER BY note_content.entity_id" in str(session.statements[1])
    assert "FOR UPDATE" in str(session.statements[2])
    assert "LEFT OUTER JOIN note_content" in str(session.statements[3])
    # The journal identities are read before the entity rows are deleted. This
    # fake returns no rows, so nothing is journaled and no position is claimed.
    assert "SELECT entity.id, entity.external_id" in str(session.statements[4])
    assert "ORDER BY note_content.entity_id" in str(session.statements[5])
    assert "SELECT DISTINCT relation.from_id" in str(session.statements[6])
    assert "DELETE FROM search_index" in str(session.statements[7])
    assert "sqlite_master" in str(session.statements[8])
    assert "DELETE FROM entity" in str(session.statements[9])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_deletes_vector_embeddings_before_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[storage_owned_delete_candidate_row(10, "notes/a.md")]
            ),
            FakeProjectIndexResult(),
            FakeProjectIndexResult(),
            FakeProjectIndexResult(
                mapping_rows=[storage_owned_delete_candidate_row(10, "notes/a.md")]
            ),
            FakeProjectIndexResult(),  # deleted entity's journal identity
            FakeProjectIndexResult(),
            FakeProjectIndexResult(scalar_values=[]),
            FakeProjectIndexResult(),
            FakeProjectIndexResult(
                scalar_values=["search_vector_chunks", "search_vector_embeddings"]
            ),
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    sqlite_vec_sessions: list[FakeProjectIndexSession] = []

    async def fake_load_sqlite_vec_on_session(
        loaded_session: FakeProjectIndexSession,
    ) -> bool:
        sqlite_vec_sessions.append(loaded_session)
        return True

    monkeypatch.setattr(
        accepted_note_vector_cleanup_module,
        "_load_sqlite_vec_on_session",
        fake_load_sqlite_vec_on_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        delete_path_verifier=project_index_maintenance_module.TrustPlannedProjectIndexDeleteVerifier(),
    )

    result = await store.apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/a.md",),
        )
    )

    assert result.deleted_entities == 1
    assert sqlite_vec_sessions == [session]
    statements = [str(statement) for statement in session.statements]
    embedding_delete_index = next(
        index
        for index, statement in enumerate(statements)
        if "DELETE FROM search_vector_embeddings" in statement
    )
    chunk_delete_index = next(
        index
        for index, statement in enumerate(statements)
        if "DELETE FROM search_vector_chunks" in statement
    )
    assert embedding_delete_index < chunk_delete_index


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_skips_vector_cleanup_when_tables_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[storage_owned_delete_candidate_row(10, "notes/a.md")]
            ),
            FakeProjectIndexResult(),
            FakeProjectIndexResult(),
            FakeProjectIndexResult(
                mapping_rows=[storage_owned_delete_candidate_row(10, "notes/a.md")]
            ),
            FakeProjectIndexResult(),  # deleted entity's journal identity
            FakeProjectIndexResult(),
            FakeProjectIndexResult(scalar_values=[]),
            FakeProjectIndexResult(),
            FakeProjectIndexResult(scalar_values=[]),
        ]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeProjectIndexSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(
        project_index_maintenance_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
        delete_path_verifier=project_index_maintenance_module.TrustPlannedProjectIndexDeleteVerifier(),
    )

    result = await store.apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/a.md",),
        )
    )

    assert result.deleted_entities == 1
    statements = [str(statement) for statement in session.statements]
    assert not any("DELETE FROM search_vector_chunks" in statement for statement in statements)
    assert not any("DELETE FROM search_vector_embeddings" in statement for statement in statements)


# --- Project change journal rows for scan moves and deletes, against a real database ---

JOURNAL_NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


async def _create_indexed_file(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    *,
    file_path: str,
    checksum: str = "file-checksum",
    content_type: str = "text/markdown",
    note_version: int | None = None,
) -> Entity:
    """Create one indexed file, with a synced note_content row when ``note_version`` is set."""
    async with db.scoped_session(session_maker) as session:
        entity = await EntityRepository(project_id=project.id).create(
            session,
            {
                "project_id": project.id,
                "title": file_path.rsplit("/", 1)[-1],
                "note_type": "note",
                "permalink": file_path.rsplit(".", 1)[0],
                "file_path": file_path,
                "checksum": checksum,
                "content_type": content_type,
                "created_at": JOURNAL_NOW,
                "updated_at": JOURNAL_NOW,
            },
        )
        if note_version is not None:
            await NoteContentRepository(project_id=project.id).create(
                session,
                NoteContent(
                    entity_id=entity.id,
                    markdown_content="# Note\n",
                    db_version=note_version,
                    db_checksum=checksum,
                    file_version=note_version,
                    file_checksum=checksum,
                    file_write_status="synced",
                    last_source="index",
                    updated_at=JOURNAL_NOW,
                    file_updated_at=JOURNAL_NOW,
                ),
            )
    return entity


async def _journal(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
) -> list[AcceptedProjectNoteChange]:
    async with db.scoped_session(session_maker) as session:
        return list(await ProjectRepository().list_accepted_note_changes(session, project.id))


async def _head(session_maker: async_sessionmaker[AsyncSession], project: Project) -> int:
    async with db.scoped_session(session_maker) as session:
        head = await ProjectRepository().get_partition_position(session, project.id)
    assert head is not None
    return head


async def _entity_ids(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
) -> set[int]:
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(select(Entity.id).where(Entity.project_id == project.id))
        return set(result.scalars())


@pytest.mark.asyncio
async def test_scan_move_journals_one_moved_row_per_markdown_note(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A rename with unchanged content is one `moved` row: no `deleted`, no `created`."""
    note = await _create_indexed_file(
        session_maker, test_project, file_path="notes/a.md", note_version=3
    )
    await _create_indexed_file(
        session_maker,
        test_project,
        file_path="assets/photo.png",
        content_type="image/png",
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=test_project.id,
    )

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(
                ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),
                ProjectIndexMoveTarget("assets/photo.png", "media/photo.png"),
            ),
        )
    )

    assert result.updated_files == 2
    [row] = await _journal(session_maker, test_project)
    assert (
        row.partition_position,
        row.operation,
        row.entity_id,
        row.note_external_id,
        row.file_path,
        row.previous_file_path,
        row.db_version,
        row.db_checksum,
        row.source,
    ) == (
        1,
        "moved",
        note.id,
        note.external_id,
        "archive/a.md",
        "notes/a.md",
        3,
        "file-checksum",
        ACCEPTED_STORAGE_CHANGE_SOURCE,
    )
    assert row.materialized_at == row.accepted_at


@pytest.mark.asyncio
async def test_scan_move_journals_replaced_destination_before_the_move(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """The entity a move replaces is journaled `deleted` first, then the note `moved` onto its path."""
    moved = await _create_indexed_file(
        session_maker, test_project, file_path="notes/a.md", note_version=1
    )
    replaced = await _create_indexed_file(
        session_maker, test_project, file_path="archive/a.md", note_version=2
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=test_project.id,
    )

    result = await store.apply_project_index_move_batch(
        ProjectIndexMoveBatch(
            completed_batches=1,
            targets=(ProjectIndexMoveTarget("notes/a.md", "archive/a.md"),),
        )
    )

    assert result.replaced_entity_ids == frozenset({replaced.id})
    assert [
        (row.partition_position, row.operation, row.entity_id, row.file_path, row.db_version)
        for row in await _journal(session_maker, test_project)
    ] == [
        (1, "deleted", replaced.id, "archive/a.md", 2),
        (2, "moved", moved.id, "archive/a.md", 1),
    ]


@pytest.mark.asyncio
async def test_scan_delete_journals_each_deleted_markdown_note(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    accepted = await _create_indexed_file(
        session_maker, test_project, file_path="notes/b.md", note_version=4
    )
    storage_owned = await _create_indexed_file(session_maker, test_project, file_path="notes/a.md")
    await _create_indexed_file(
        session_maker,
        test_project,
        file_path="assets/photo.png",
        content_type="image/png",
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=test_project.id,
        delete_path_verifier=project_index_maintenance_module.TrustPlannedProjectIndexDeleteVerifier(),
    )

    result = await store.apply_project_index_delete_batch(
        ProjectIndexDeleteBatch(
            completed_batches=1,
            paths=("notes/b.md", "assets/photo.png", "notes/a.md"),
        )
    )

    assert result.deleted_entities == 3
    rows = await _journal(session_maker, test_project)
    # Path order, so a batch replays the same way every time.
    assert [
        (
            row.partition_position,
            row.operation,
            row.entity_id,
            row.file_path,
            row.db_version,
            row.db_checksum,
            row.source,
        )
        for row in rows
    ] == [
        (1, "deleted", storage_owned.id, "notes/a.md", None, None, ACCEPTED_STORAGE_CHANGE_SOURCE),
        (
            2,
            "deleted",
            accepted.id,
            "notes/b.md",
            4,
            "file-checksum",
            ACCEPTED_STORAGE_CHANGE_SOURCE,
        ),
    ]
    assert all(row.materialized_at == row.accepted_at for row in rows)


@pytest.mark.asyncio
async def test_scan_delete_journal_rolls_back_with_failed_transaction(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the journal write keeps the entity, the journal and the head unchanged."""
    note = await _create_indexed_file(
        session_maker, test_project, file_path="notes/a.md", note_version=1
    )
    record_storage_note_changes = project_index_maintenance_module.record_storage_note_changes

    async def record_then_fail(session: AsyncSession, **kwargs: Any) -> None:
        await record_storage_note_changes(session, **kwargs)
        raise RuntimeError("simulated failure after the journal write")

    monkeypatch.setattr(
        project_index_maintenance_module, "record_storage_note_changes", record_then_fail
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=test_project.id,
        delete_path_verifier=project_index_maintenance_module.TrustPlannedProjectIndexDeleteVerifier(),
    )

    with pytest.raises(RuntimeError, match="simulated failure after the journal write"):
        await store.apply_project_index_delete_batch(
            ProjectIndexDeleteBatch(completed_batches=1, paths=("notes/a.md",))
        )

    assert note.id in await _entity_ids(session_maker, test_project)
    assert await _journal(session_maker, test_project) == []
    assert await _head(session_maker, test_project) == 0


# --- A move that rewrites frontmatter settles its journal row only once the write lands ---

PLANNED_REWRITE = project_index_maintenance_module.ProjectIndexMovedFileContentUpdate(
    permalink="archive/a",
    checksum="rewritten-checksum",
    markdown_content="---\npermalink: archive/a\n---\n\n# Note\n",
)


@dataclass(slots=True)
class JournalObservingMoveContentUpdater:
    """Plan one frontmatter rewrite, and record the journal as each post-commit write sees it."""

    session_maker: async_sessionmaker[AsyncSession]
    project: Project
    rewritten_entity_id: int
    fail_writes: bool = False
    stored_checksum: str | None = None
    # (file_path, materialized) of every journal row, read when a write starts.
    journal_during_write: list[list[tuple[str, bool]]] = field(default_factory=list)

    async def plan_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: project_index_maintenance_module.ProjectIndexMovedFile,
    ) -> project_index_maintenance_module.ProjectIndexMovedFileContentUpdate | None:
        del session
        if moved_file.entity_id != self.rewritten_entity_id:
            return None
        return PLANNED_REWRITE

    async def write_moved_file_content(
        self,
        moved_file: project_index_maintenance_module.ProjectIndexMovedFile,
        content_update: project_index_maintenance_module.ProjectIndexMovedFileContentUpdate,
    ) -> str:
        del moved_file
        self.journal_during_write.append(
            [
                (row.file_path, row.materialized_at is not None)
                for row in await _journal(self.session_maker, self.project)
            ]
        )
        if self.fail_writes:
            raise OSError("simulated disk full")
        return self.stored_checksum or content_update.checksum


def _projector_pending_positions(rows: Sequence[AcceptedProjectNoteChange]) -> tuple[int, ...]:
    """Plan a full wiki rebuild over these journal rows; return the positions it waits on."""
    head = max(row.partition_position for row in rows)
    snapshot = WikiProjectionSnapshot(
        project_id="project",
        project_name="Project",
        source_partition_position=head,
        current_output_watermark=0,
        source_accepted_at=max(row.accepted_at for row in rows),
        notes=(),
        changes=tuple(
            WikiSourceChange(
                partition_position=row.partition_position,
                operation=WikiChangeOperation(row.operation),
                path=row.file_path,
                permalink=row.permalink,
                previous_path=row.previous_file_path,
                title=row.title,
                accepted_at=row.accepted_at,
                materialized=row.materialized_at is not None,
                source=row.source,
            )
            for row in rows
        ),
    )
    request = WikiProjectionRequest(
        project_id="project",
        through_partition_position=head,
        projector_version=WIKI_PROJECTOR_VERSION,
        reason=WikiProjectionReason.manual_rebuild,
    )
    return plan_wiki_projection(request, snapshot).result.pending_materialization


async def _move_with_rewrite(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    **updater_options: Any,
) -> tuple[Entity, Entity, JournalObservingMoveContentUpdater]:
    """Move two notes in one batch; only notes/a.md gets a planned frontmatter rewrite."""
    rewritten = await _create_indexed_file(
        session_maker, project, file_path="notes/a.md", note_version=3
    )
    unchanged = await _create_indexed_file(
        session_maker, project, file_path="notes/b.md", note_version=1
    )
    updater = JournalObservingMoveContentUpdater(
        session_maker=session_maker,
        project=project,
        rewritten_entity_id=rewritten.id,
        **updater_options,
    )
    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=project.id,
        move_content_updater=updater,
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
    assert result.updated_files == 2
    return rewritten, unchanged, updater


@pytest.mark.asyncio
async def test_scan_move_settles_a_rewritten_note_row_only_after_its_write(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """The rewritten note's row is unsettled while its bytes are written, then settled."""
    rewritten, unchanged, updater = await _move_with_rewrite(session_maker, test_project)

    # During the post-commit write, only the move with unchanged content was settled.
    assert updater.journal_during_write == [[("archive/a.md", False), ("archive/b.md", True)]]
    rows = await _journal(session_maker, test_project)
    assert [
        (row.operation, row.entity_id, row.permalink, row.db_checksum, row.materialized_at is None)
        for row in rows
    ] == [
        ("moved", rewritten.id, "archive/a", "rewritten-checksum", False),
        ("moved", unchanged.id, "notes/b", "file-checksum", False),
    ]
    assert _projector_pending_positions(rows) == ()


@pytest.mark.asyncio
async def test_scan_move_keeps_a_rewritten_note_row_unsettled_when_its_write_fails(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A failed rewrite never reports its checksum as materialized; the next scan settles it."""
    rewritten, _, _ = await _move_with_rewrite(session_maker, test_project, fail_writes=True)

    rows = await _journal(session_maker, test_project)
    [moved_row] = [row for row in rows if row.entity_id == rewritten.id]
    assert (moved_row.db_checksum, moved_row.materialized_at) == ("rewritten-checksum", None)
    assert _projector_pending_positions(rows) == (moved_row.partition_position,)

    # The next scan reads the file's real bytes, which never received the rewrite,
    # and accepts them as a new version. That settled row settles the move too.
    async with db.scoped_session(session_maker) as session:
        entity = await session.get(Entity, rewritten.id)
    assert entity is not None
    result = await NoteContentReconciler(
        note_content_repository=NoteContentRepository(project_id=test_project.id),
        session_maker=session_maker,
    ).reconcile(entity=entity, markdown_content="# Note\n", observed_at=None, source="index")

    assert (result.status, result.generation) == ("current", 4)
    rows = await _journal(session_maker, test_project)
    assert [(row.operation, row.entity_id, row.db_version) for row in rows][-1] == (
        "updated",
        rewritten.id,
        4,
    )
    assert all(row.materialized_at is not None for row in rows)
    assert _projector_pending_positions(rows) == ()


@pytest.mark.asyncio
async def test_scan_move_keeps_a_rewritten_note_row_unsettled_when_storage_holds_other_bytes(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A write that stores different bytes than planned, such as CRLF newlines, settles nothing."""
    rewritten, _, _ = await _move_with_rewrite(
        session_maker, test_project, stored_checksum="crlf-checksum"
    )

    [moved_row] = [
        row for row in await _journal(session_maker, test_project) if row.entity_id == rewritten.id
    ]
    assert moved_row.materialized_at is None


@pytest.mark.asyncio
async def test_scan_move_logs_a_failed_settle_and_keeps_the_committed_move(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database failure while settling is logged; the move stays committed and its row unsettled."""

    async def fail_settle(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(project_index_maintenance_module, "settle_stored_note_changes", fail_settle)

    rewritten, _, updater = await _move_with_rewrite(session_maker, test_project)

    assert len(updater.journal_during_write) == 1
    [moved_row] = [
        row for row in await _journal(session_maker, test_project) if row.entity_id == rewritten.id
    ]
    assert (moved_row.file_path, moved_row.materialized_at) == ("archive/a.md", None)
