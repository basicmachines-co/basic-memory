"""Tests for project-index move/delete maintenance."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast

import basic_memory.indexing.project_index_maintenance as project_index_maintenance_module
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing import (
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
    StoreProjectIndexMaintenanceRunner,
    build_project_index_delete_batch_plan,
    build_project_index_move_batch_plan,
    run_project_index_delete_batches,
    run_project_index_move_batches,
)
from basic_memory.runtime import RuntimeWorkflowMetadataPatch


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
