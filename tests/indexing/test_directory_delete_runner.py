"""Tests for portable directory-delete cleanup orchestration."""

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import basic_memory.indexing.directory_delete_runner as directory_delete_runner_module
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete

from basic_memory import db
from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteAcceptanceRequest,
    DirectoryDeleteAcceptedResult,
    DirectoryDeleteFileFailure,
    DirectoryDeleteRejected,
    DirectoryDeleteRejectKind,
    DirectoryEntityDeleteResult,
    DirectoryFileDeleteEnqueueError,
    RepositoryDirectoryDeleteAcceptanceStore,
    DirectoryDeleteRuntime,
    enqueue_directory_file_delete_jobs,
    normalize_directory_delete_path,
    run_directory_delete,
)
from basic_memory.models import Entity, NoteContent, Relation
from basic_memory.runtime.cleanup import (
    RuntimeDirectoryFileSnapshot,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
)
from basic_memory.schemas.response import DirectoryDeleteError, DirectoryDeleteResult


class FakeDirectoryFileDeleteEnqueuer:
    def __init__(self) -> None:
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        self.requests.append(request)


class FakeDirectoryDeleteStore:
    def __init__(
        self,
        *,
        project_id: int | None = 3,
        files: list[RuntimeDirectoryFileSnapshot] | None = None,
        relation_cleanup_entity_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.project_id = project_id
        self.files = files or []
        self.relation_cleanup_entity_ids = relation_cleanup_entity_ids
        self.loaded_directories: list[str] = []
        self.deleted_directories: list[str] = []
        self.deleted_entity_ids: list[tuple[int, ...]] = []

    async def load_project_id(
        self,
        session: AsyncSession,
        project_external_id: str,
    ) -> int | None:
        return self.project_id

    async def load_directory_file_snapshots(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        directory: str,
    ) -> list[RuntimeDirectoryFileSnapshot]:
        self.loaded_directories.append(directory)
        return self.files

    async def delete_directory_entities(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        directory: str,
        entity_ids: Sequence[int],
    ) -> DirectoryEntityDeleteResult:
        assert project_id == self.project_id
        self.deleted_directories.append(directory)
        self.deleted_entity_ids.append(tuple(entity_ids))
        return DirectoryEntityDeleteResult(
            deleted_entity_ids=frozenset(entity_ids),
            relation_cleanup_entity_ids=self.relation_cleanup_entity_ids,
        )


class FakeScalarResult:
    def __init__(
        self,
        value: object | None,
        values: list[object] | None = None,
    ) -> None:
        self.value = value
        self.values = values or ([] if value is None else [value])

    def one_or_none(self) -> object | None:
        return self.value

    def __iter__(self):
        return iter(self.values)


class FakeExecuteResult:
    def __init__(
        self,
        *,
        scalar_value: object | None = None,
        scalar_values: list[object] | None = None,
        rows: list[object] | None = None,
    ):
        self.scalar_value = scalar_value
        self.scalar_values = scalar_values
        self.rows = rows or []

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self.scalar_value, self.scalar_values)

    def tuples(self) -> "FakeExecuteResult":
        return self

    def all(self) -> list[object]:
        return self.rows


class FakeExecuteSession:
    def __init__(self, results: list[FakeExecuteResult]) -> None:
        self.results = results
        self.queries: list[tuple[object, object | None]] = []

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def execute(self, query: object, params: object | None = None) -> FakeExecuteResult:
        self.queries.append((query, params))
        return self.results.pop(0)


def directory_snapshot(
    *,
    entity_id: int = 7,
    file_path: str = "notes/example.md",
    file_checksum: str | None = "note-sha",
) -> RuntimeDirectoryFileSnapshot:
    return RuntimeDirectoryFileSnapshot(
        entity_id=entity_id,
        file_path=file_path,
        file_checksum=file_checksum,
        last_modified_at=160.0,
        size=None,
    )


def test_directory_delete_result_serializes_empty_complete_shape() -> None:
    result = DirectoryDeleteAcceptedResult.complete()

    assert result.http_status_code == 200
    assert result.to_response_payload() == {
        "total_files": 0,
        "successful_deletes": 0,
        "failed_deletes": 0,
        "deleted_files": [],
        "errors": [],
        "file_delete_status": "complete",
    }


def test_directory_delete_result_serializes_pending_deleted_files() -> None:
    result = DirectoryDeleteAcceptedResult.pending(
        deleted_files=("notes/a.md", "notes/b.md"),
    )

    assert result.to_response_payload() == {
        "total_files": 2,
        "successful_deletes": 2,
        "failed_deletes": 0,
        "deleted_files": ["notes/a.md", "notes/b.md"],
        "errors": [],
        "file_delete_status": "pending",
    }


def test_directory_delete_result_serializes_skipped_files_as_failures() -> None:
    # A guarded cleanup that left a file on disk must be reported as failed, not
    # folded into successful_deletes (the file would otherwise reappear silently).
    result = DirectoryDeleteAcceptedResult.pending(
        deleted_files=("notes/a.md", "notes/b.md"),
        failed_files=(
            DirectoryDeleteFileFailure(
                file_path="notes/b.md",
                reason="file changed before delete: notes/b.md",
            ),
        ),
    )

    assert result.to_response_payload() == {
        "total_files": 2,
        "successful_deletes": 1,
        "failed_deletes": 1,
        "deleted_files": ["notes/a.md", "notes/b.md"],
        "errors": [{"path": "notes/b.md", "error": "file changed before delete: notes/b.md"}],
        "file_delete_status": "pending",
    }


def test_directory_delete_payload_with_failures_validates_as_client_result() -> None:
    """The MCP/CLI client validates the payload as DirectoryDeleteResult, whose
    errors field requires {path, error} objects — plain strings raised a Pydantic
    validation error on every partial failure."""
    result = DirectoryDeleteAcceptedResult.pending(
        deleted_files=("notes/a.md", "notes/b.md"),
        failed_files=(
            DirectoryDeleteFileFailure(
                file_path="notes/b.md",
                reason="file changed before delete: notes/b.md",
            ),
        ),
    )

    validated = DirectoryDeleteResult.model_validate(result.to_response_payload())

    assert validated.failed_deletes == 1
    assert validated.errors == [
        DirectoryDeleteError(
            path="notes/b.md",
            error="file changed before delete: notes/b.md",
        )
    ]


def test_directory_delete_result_serializes_failed_enqueue_error() -> None:
    result = DirectoryDeleteAcceptedResult.failed(
        deleted_files=("notes/a.md",),
        error="queue unavailable",
    )

    # Files remain on disk with their DB rows gone: the route must report 500.
    assert result.http_status_code == 500
    assert result.to_response_payload() == {
        "total_files": 1,
        "successful_deletes": 1,
        "failed_deletes": 0,
        "deleted_files": ["notes/a.md"],
        "errors": [],
        "file_delete_status": "failed",
        "error": "queue unavailable",
    }


def test_normalize_directory_delete_path_allows_root_and_trims_slashes() -> None:
    assert normalize_directory_delete_path("/") == ""
    assert normalize_directory_delete_path("/notes/recipes/") == "notes/recipes"


def test_normalize_directory_delete_path_rejects_project_traversal() -> None:
    with pytest.raises(ValueError, match="Invalid directory path"):
        normalize_directory_delete_path("notes/../other")


@pytest.mark.asyncio
async def test_enqueue_directory_file_delete_jobs_maps_runtime_snapshots() -> None:
    enqueuer = FakeDirectoryFileDeleteEnqueuer()

    await enqueue_directory_file_delete_jobs(
        project_id=3,
        files=[
            directory_snapshot(entity_id=7, file_path="notes/example.md"),
            directory_snapshot(
                entity_id=8,
                file_path="notes/legacy.md",
                file_checksum=None,
            ),
        ],
        enqueuer=enqueuer,
    )

    assert enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=3,
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="note-sha",
        ),
        RuntimeNoteFileDeleteJobRequest(
            project_id=3,
            entity_id=8,
            file_path="notes/legacy.md",
            file_checksum=None,
        ),
    ]


@pytest.mark.asyncio
async def test_run_directory_delete_accepts_rows_and_queues_cleanup() -> None:
    files = [
        directory_snapshot(entity_id=7, file_path="notes/a.md"),
        directory_snapshot(entity_id=8, file_path="notes/b.md", file_checksum=None),
    ]
    store = FakeDirectoryDeleteStore(project_id=3, files=files)
    enqueuer = FakeDirectoryFileDeleteEnqueuer()

    result = await run_directory_delete(
        AsyncSession(),
        request=DirectoryDeleteAcceptanceRequest(
            project_external_id="project-123",
            directory="/notes/",
        ),
        runtime=DirectoryDeleteRuntime(store=store, file_delete_enqueuer=enqueuer),
    )

    assert result == DirectoryDeleteAcceptedResult.pending(
        deleted_files=("notes/a.md", "notes/b.md")
    )
    assert store.loaded_directories == ["notes"]
    assert store.deleted_directories == ["notes"]
    assert store.deleted_entity_ids == [(7, 8)]
    # No guarded skips here, so the payload still reports a clean success.
    assert result.to_response_payload()["failed_deletes"] == 0
    assert enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=3,
            entity_id=7,
            file_path="notes/a.md",
            file_checksum="note-sha",
        ),
        RuntimeNoteFileDeleteJobRequest(
            project_id=3,
            entity_id=8,
            file_path="notes/b.md",
            file_checksum=None,
        ),
    ]


@pytest.mark.asyncio
async def test_run_directory_delete_reports_skipped_guarded_cleanup() -> None:
    """A guarded inline delete that skips a file (checksum changed) must surface as
    a failed delete with the reason, not a silent success that strands the file."""
    files = [directory_snapshot(entity_id=7, file_path="notes/a.md")]
    store = FakeDirectoryDeleteStore(project_id=3, files=files)

    class SkippingEnqueuer:
        async def enqueue_directory_file_delete(
            self,
            request: RuntimeNoteFileDeleteJobRequest,
        ) -> RuntimeFileDeleteResult:
            return RuntimeFileDeleteResult.no_accepted_checksum(
                file_path=request.file_path,
                entity_id=request.entity_id,
            )

    result = await run_directory_delete(
        AsyncSession(),
        request=DirectoryDeleteAcceptanceRequest(
            project_external_id="project-123",
            directory="/notes/",
        ),
        runtime=DirectoryDeleteRuntime(store=store, file_delete_enqueuer=SkippingEnqueuer()),
    )

    assert result.failed_files == (
        DirectoryDeleteFileFailure(
            file_path="notes/a.md",
            reason="no accepted file checksum for notes/a.md",
        ),
    )
    payload = result.to_response_payload()
    assert payload["successful_deletes"] == 0
    assert payload["failed_deletes"] == 1
    assert payload["errors"] == [
        {"path": "notes/a.md", "error": "no accepted file checksum for notes/a.md"}
    ]


@pytest.mark.asyncio
async def test_run_directory_delete_continues_past_enqueue_failure() -> None:
    """One file whose cleanup can't be queued must not abort cleanup of the rest.

    The failing file's entity row is already deleted, so aborting would strand every
    later file on disk with its DB row gone (sync would then resurrect it). The batch
    keeps going and the failure is reported as a failed delete, not raised.
    """
    files = [
        directory_snapshot(entity_id=7, file_path="notes/a.md"),
        directory_snapshot(entity_id=8, file_path="notes/b.md"),
        directory_snapshot(entity_id=9, file_path="notes/c.md"),
    ]
    store = FakeDirectoryDeleteStore(project_id=3, files=files)

    class PartiallyFailingEnqueuer:
        def __init__(self) -> None:
            self.attempted: list[str] = []

        async def enqueue_directory_file_delete(
            self,
            request: RuntimeNoteFileDeleteJobRequest,
        ) -> RuntimeFileDeleteResult | None:
            self.attempted.append(request.file_path)
            if request.file_path == "notes/b.md":
                raise DirectoryFileDeleteEnqueueError("queue unavailable for notes/b.md")
            return None

    enqueuer = PartiallyFailingEnqueuer()
    result = await run_directory_delete(
        AsyncSession(),
        request=DirectoryDeleteAcceptanceRequest(
            project_external_id="project-123",
            directory="/notes/",
        ),
        runtime=DirectoryDeleteRuntime(store=store, file_delete_enqueuer=enqueuer),
    )

    # Every file was attempted despite the middle failure.
    assert enqueuer.attempted == ["notes/a.md", "notes/b.md", "notes/c.md"]
    # The failing file is reported as a failed delete, not raised out of the batch.
    assert result.failed_files == (
        DirectoryDeleteFileFailure(
            file_path="notes/b.md",
            reason="queue unavailable for notes/b.md",
        ),
    )
    payload = result.to_response_payload()
    assert payload["file_delete_status"] == "pending"
    assert payload["successful_deletes"] == 2
    assert payload["failed_deletes"] == 1
    assert payload["errors"] == [
        {"path": "notes/b.md", "error": "queue unavailable for notes/b.md"}
    ]


@pytest.mark.asyncio
async def test_run_directory_delete_surfaces_relation_cleanup_sources() -> None:
    """Surviving notes that linked into the deleted directory must be surfaced so the
    caller can reindex them and drop their now-stale relation rows from search."""
    files = [directory_snapshot(entity_id=7, file_path="notes/a.md")]
    store = FakeDirectoryDeleteStore(
        project_id=3,
        files=files,
        relation_cleanup_entity_ids=frozenset({42, 99}),
    )

    result = await run_directory_delete(
        AsyncSession(),
        request=DirectoryDeleteAcceptanceRequest(
            project_external_id="project-123",
            directory="/notes/",
        ),
        runtime=DirectoryDeleteRuntime(
            store=store,
            file_delete_enqueuer=FakeDirectoryFileDeleteEnqueuer(),
        ),
    )

    assert result.relation_cleanup_entity_ids == frozenset({42, 99})


@pytest.mark.asyncio
async def test_repository_directory_delete_store_captures_relation_sources() -> None:
    """The repository store captures incoming relation sources before deleting rows so
    the surviving (outside-directory) sources can be reindexed after CASCADE."""
    session = cast(
        AsyncSession,
        FakeExecuteSession(
            [
                FakeExecuteResult(),  # sorted note_content lock fence
                FakeExecuteResult(scalar_values=[7, 8]),  # current directory members
                FakeExecuteResult(rows=[(101, 7, 42), (102, 8, 99)]),
                FakeExecuteResult(scalar_values=[7, 8]),  # guarded entity delete
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_values=[]),  # vector rows
            ]
        ),
    )
    store = RepositoryDirectoryDeleteAcceptanceStore()

    delete_result = await store.delete_directory_entities(
        session,
        project_id=3,
        directory="notes",
        entity_ids=[7, 8],
    )

    assert delete_result == DirectoryEntityDeleteResult(
        deleted_entity_ids=frozenset({7, 8}),
        relation_cleanup_entity_ids=frozenset({42, 99}),
    )


@pytest.mark.asyncio
async def test_run_directory_delete_rejects_unknown_project() -> None:
    with pytest.raises(DirectoryDeleteRejected) as exc_info:
        await run_directory_delete(
            AsyncSession(),
            request=DirectoryDeleteAcceptanceRequest(
                project_external_id="missing-project",
                directory="notes",
            ),
            runtime=DirectoryDeleteRuntime(
                store=FakeDirectoryDeleteStore(project_id=None),
                file_delete_enqueuer=FakeDirectoryFileDeleteEnqueuer(),
            ),
        )

    assert exc_info.value.rejection.kind is DirectoryDeleteRejectKind.not_found
    assert exc_info.value.rejection.detail == "Project 'missing-project' not found"


@pytest.mark.asyncio
async def test_repository_directory_delete_store_maps_note_content_snapshots() -> None:
    session = cast(
        AsyncSession,
        FakeExecuteSession(
            [
                FakeExecuteResult(scalar_value=3),
                FakeExecuteResult(
                    rows=[
                        type(
                            "Row",
                            (),
                            {
                                "id": 7,
                                "file_path": "notes/example.md",
                                "checksum": "entity-sha",
                                "mtime": 100.0,
                                "size": 42,
                                "note_file_checksum": "note-sha",
                                "note_file_updated_at": None,
                            },
                        )()
                    ]
                ),
                FakeExecuteResult(),  # sorted note_content lock fence
                FakeExecuteResult(scalar_values=[7]),  # current directory members
                FakeExecuteResult(rows=[]),  # incoming relation snapshot
                FakeExecuteResult(scalar_values=[7]),  # guarded entity delete
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_values=[]),  # vector rows
            ]
        ),
    )
    fake_session = cast(FakeExecuteSession, session)
    store = RepositoryDirectoryDeleteAcceptanceStore()

    project_id = await store.load_project_id(
        session,
        "project-123",
    )
    snapshots = await store.load_directory_file_snapshots(
        session,
        project_id=3,
        directory="notes",
    )
    delete_result = await store.delete_directory_entities(
        session,
        project_id=3,
        directory="notes",
        entity_ids=[7],
    )

    assert project_id == 3
    assert snapshots == [
        RuntimeDirectoryFileSnapshot(
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="entity-sha",
            last_modified_at=100.0,
            size=42,
        )
    ]
    assert delete_result == DirectoryEntityDeleteResult(
        deleted_entity_ids=frozenset({7}),
    )
    assert len(fake_session.queries) == 8
    assert "FOR UPDATE" not in str(fake_session.queries[1][0])
    assert "ORDER BY note_content.entity_id" in str(fake_session.queries[2][0])
    assert "entity.file_path LIKE" in str(fake_session.queries[3][0])
    guarded_entity_delete = str(fake_session.queries[5][0])
    assert "entity.id IN" in guarded_entity_delete
    assert "entity.project_id" in guarded_entity_delete
    assert "entity.file_path LIKE" in guarded_entity_delete
    assert "NOT (EXISTS" in guarded_entity_delete


@pytest.mark.asyncio
async def test_repository_directory_delete_store_clears_vectors_for_deleted_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(
        AsyncSession,
        FakeExecuteSession(
            [
                FakeExecuteResult(),  # sorted note_content lock fence
                FakeExecuteResult(scalar_values=[7, 8]),  # current directory members
                FakeExecuteResult(rows=[]),  # incoming relation snapshot
                FakeExecuteResult(scalar_values=[7, 8]),  # guarded entity delete
                FakeExecuteResult(),  # search_index delete
            ]
        ),
    )
    fake_session = cast(FakeExecuteSession, session)
    vector_calls: list[tuple[AsyncSession, int, tuple[int, ...], int]] = []

    async def fake_delete_project_index_vector_rows(
        cleanup_session: AsyncSession,
        *,
        project_id: int,
        entity_ids: Sequence[int],
    ) -> None:
        vector_calls.append(
            (
                cleanup_session,
                project_id,
                tuple(entity_ids),
                len(fake_session.queries),
            )
        )

    monkeypatch.setattr(
        directory_delete_runner_module,
        "delete_project_index_vector_rows",
        fake_delete_project_index_vector_rows,
        raising=False,
    )

    store = RepositoryDirectoryDeleteAcceptanceStore()

    await store.delete_directory_entities(
        session,
        project_id=3,
        directory="notes",
        entity_ids=[7, 8],
    )

    # Projection cleanup uses only ids returned by the guarded Entity mutation.
    assert vector_calls == [(session, 3, (7, 8), 5)]
    statements = [str(query) for query, _ in fake_session.queries]
    assert "ORDER BY note_content.entity_id" in statements[0]
    assert "DELETE FROM entity" in statements[3]
    assert "DELETE FROM search_index" in statements[4]


@pytest.mark.asyncio
async def test_repository_directory_delete_revalidates_membership_after_move_out(
    session_maker,
    test_project,
) -> None:
    """A note moved after the snapshot is not authorized by its stale entity id."""
    now = datetime.now(tz=UTC)
    async with db.scoped_session(session_maker) as session:
        moved_note = Entity(
            project_id=test_project.id,
            title="Move-out survivor",
            note_type="note",
            permalink="notes/move-out-survivor",
            file_path="notes/move-out-survivor.md",
            content_type="text/markdown",
            created_at=now,
            updated_at=now,
        )
        session.add(moved_note)
        await session.flush()
        moved_note_id = moved_note.id
        session.add(
            NoteContent(
                entity_id=moved_note_id,
                project_id=test_project.id,
                external_id=moved_note.external_id,
                file_path=moved_note.file_path,
                markdown_content="# Move-out survivor\n",
                db_version=1,
                db_checksum="generation-1",
                file_write_status="synced",
            )
        )

    store = RepositoryDirectoryDeleteAcceptanceStore()
    async with db.scoped_session(session_maker) as session:
        snapshots = await store.load_directory_file_snapshots(
            session,
            project_id=test_project.id,
            directory="notes",
        )
    assert [snapshot.entity_id for snapshot in snapshots] == [moved_note_id]

    async with db.scoped_session(session_maker) as session:
        moved_note = await session.get(Entity, moved_note_id)
        note_content = await session.get(NoteContent, moved_note_id)
        assert moved_note is not None
        assert note_content is not None
        moved_note.file_path = "archive/move-out-survivor.md"
        note_content.file_path = moved_note.file_path

    async with db.scoped_session(session_maker) as session:
        delete_result = await store.delete_directory_entities(
            session,
            project_id=test_project.id,
            directory="notes",
            entity_ids=[snapshot.entity_id for snapshot in snapshots],
        )

    assert delete_result == DirectoryEntityDeleteResult()
    async with db.scoped_session(session_maker) as session:
        moved_note = await session.get(Entity, moved_note_id)
        note_content = await session.get(NoteContent, moved_note_id)
    assert moved_note is not None
    assert moved_note.file_path == "archive/move-out-survivor.md"
    assert note_content is not None
    assert note_content.file_path == "archive/move-out-survivor.md"


@pytest.mark.asyncio
async def test_repository_directory_delete_rejects_uncaptured_incoming_relation(
    session_maker,
    test_project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relation resolved after capture keeps its target and repair source intact."""
    now = datetime.now(tz=UTC)
    async with db.scoped_session(session_maker) as session:
        source = Entity(
            project_id=test_project.id,
            title="Surviving source",
            note_type="note",
            file_path="outside/source.md",
            content_type="text/markdown",
            created_at=now,
            updated_at=now,
        )
        target = Entity(
            project_id=test_project.id,
            title="Delete target",
            note_type="note",
            file_path="notes/target.md",
            content_type="text/markdown",
            created_at=now,
            updated_at=now,
        )
        session.add_all([source, target])
        await session.flush()
        session.add(
            NoteContent(
                entity_id=target.id,
                project_id=test_project.id,
                external_id=target.external_id,
                file_path=target.file_path,
                markdown_content="# Delete target\n",
                db_version=1,
                db_checksum="generation-1",
                file_write_status="synced",
            )
        )
        relation = Relation(
            project_id=test_project.id,
            from_id=source.id,
            to_id=None,
            to_name=target.title,
            relation_type="related_to",
            generation=0,
        )
        session.add(relation)
        await session.flush()
        target_id = target.id
        relation_id = relation.id

    store = RepositoryDirectoryDeleteAcceptanceStore()
    async with db.scoped_session(session_maker) as session:
        snapshots = await store.load_directory_file_snapshots(
            session,
            project_id=test_project.id,
            directory="notes",
        )
    assert [snapshot.entity_id for snapshot in snapshots] == [target_id]

    async with db.scoped_session(session_maker) as session:
        original_execute = session.execute
        relation_resolved = False

        async def execute_with_relation_race(statement, *args, **kwargs):
            nonlocal relation_resolved
            if (
                not relation_resolved
                and isinstance(statement, Delete)
                and statement.table.name == Entity.__tablename__
            ):
                relation_resolved = True
                await original_execute(
                    update(Relation).where(Relation.id == relation_id).values(to_id=target_id)
                )
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", execute_with_relation_race)
        delete_result = await store.delete_directory_entities(
            session,
            project_id=test_project.id,
            directory="notes",
            entity_ids=[target_id],
        )

    assert relation_resolved is True
    assert delete_result == DirectoryEntityDeleteResult()
    async with db.scoped_session(session_maker) as session:
        target = await session.get(Entity, target_id)
        note_content = await session.get(NoteContent, target_id)
        relation = await session.get(Relation, relation_id)
    assert target is not None
    assert note_content is not None
    assert relation is not None
    assert relation.to_id == target_id
