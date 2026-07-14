"""Tests for project-index move/delete maintenance."""

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast

import basic_memory.indexing.project_index_maintenance as project_index_maintenance_module
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from basic_memory.models import Entity


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
    """Record moved-file repair requests and return configured content updates."""

    updates: dict[int, project_index_maintenance_module.ProjectIndexMovedFileContentUpdate]
    seen_files: list[project_index_maintenance_module.ProjectIndexMovedFile] = field(
        default_factory=list
    )

    async def update_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: project_index_maintenance_module.ProjectIndexMovedFile,
    ) -> project_index_maintenance_module.ProjectIndexMovedFileContentUpdate | None:
        del session
        self.seen_files.append(moved_file)
        return self.updates.get(moved_file.entity_id)


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
    assert len(session.statements) == 9
    assert "SELECT entity.id, entity.file_path" in str(session.statements[0])
    assert "SELECT entity.id, entity.file_path" in str(session.statements[1])
    assert "SELECT DISTINCT relation.from_id" in str(session.statements[2])
    assert "DELETE FROM search_index" in str(session.statements[3])
    assert "sqlite_master" in str(session.statements[4])
    assert "DELETE FROM entity" in str(session.statements[5])
    assert "UPDATE entity" in str(session.statements[6])
    assert "UPDATE note_content" in str(session.statements[7])
    assert "UPDATE search_index" in str(session.statements[8])


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
    assert len(session.statements) == 6
    assert "checksum" in str(session.statements[2])
    assert "permalink" in str(session.statements[2])
    assert "markdown_content" in str(session.statements[3])
    assert "db_checksum" in str(session.statements[3])
    assert "file_checksum" in str(session.statements[3])
    assert "UPDATE search_index" in str(session.statements[4])
    assert "search_index.type" in str(session.statements[5])
    assert "permalink" in str(session.statements[5])


@dataclass(frozen=True, slots=True)
class StaticDeletePathVerifier:
    """Confirm only the configured paths as still absent from storage."""

    confirmed: frozenset[str]

    async def confirm_deleted_paths(self, paths: Sequence[str]) -> frozenset[str]:
        return frozenset(path for path in paths if path in self.confirmed)


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
                    {"id": 10, "file_path": "notes/a.md"},
                ]
            ),
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
    session = FakeProjectIndexSession(results=[])

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
    assert session.statements == []


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
        project_index_maintenance_module.db,
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
    assert "sqlite_master" in str(session.statements[3])
    assert "DELETE FROM entity" in str(session.statements[4])


@pytest.mark.asyncio
async def test_repository_project_index_maintenance_store_deletes_vector_embeddings_before_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeProjectIndexSession(
        results=[
            FakeProjectIndexResult(
                mapping_rows=[
                    {"id": 10, "file_path": "notes/a.md"},
                ]
            ),
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
        project_index_maintenance_module,
        "_load_sqlite_vec_on_session",
        fake_load_sqlite_vec_on_session,
    )

    store = RepositoryProjectIndexMaintenanceStore(
        session_maker=session_maker,
        project_id=42,
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
                mapping_rows=[
                    {"id": 10, "file_path": "notes/a.md"},
                ]
            ),
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
