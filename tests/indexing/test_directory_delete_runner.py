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
    ACCEPTED_DIRECTORY_DELETE_SOURCE,
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
from basic_memory.index.local_notes import LocalDirectoryFileDeleteEnqueuer
from basic_memory.index.local_wiki_projection import inspect_local_wiki_projection
from basic_memory.models import AcceptedProjectNoteChange, Entity, NoteContent, Project, Relation
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.directory_deletes import DirectoryDeleteService
from basic_memory.runtime.project_partition import RuntimeProjectNoteOperation
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
        project_external_id: str,
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

    def scalar_one_or_none(self) -> object | None:
        return self.scalar_value

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


def deleted_entity_row(
    entity_id: int,
    *,
    file_path: str | None = None,
    content_type: str = "text/markdown",
) -> SimpleNamespace:
    """One row of the guarded delete's RETURNING clause."""
    path = file_path or f"notes/note-{entity_id}.md"
    return SimpleNamespace(
        id=entity_id,
        external_id=f"note-external-{entity_id}",
        permalink=path.removesuffix(".md"),
        title=f"Note {entity_id}",
        file_path=path,
        content_type=content_type,
    )


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
                FakeExecuteResult(rows=[]),  # canonical lock check
                FakeExecuteResult(rows=[(101, 7, 42), (102, 8, 99)]),
                FakeExecuteResult(
                    rows=[deleted_entity_row(7), deleted_entity_row(8)]
                ),  # guarded entity delete
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_values=[]),  # vector rows
                FakeExecuteResult(scalar_value=12),  # journal position claim
                FakeExecuteResult(),  # journal insert
            ]
        ),
    )
    store = RepositoryDirectoryDeleteAcceptanceStore()

    delete_result = await store.delete_directory_entities(
        session,
        project_id=3,
        project_external_id="project-123",
        directory="notes",
        entity_ids=[7, 8],
    )

    assert delete_result.deleted_entity_ids == frozenset({7, 8})
    assert delete_result.relation_cleanup_entity_ids == frozenset({42, 99})
    # Head 12 after claiming two positions: the notes take 11 and 12 in path order.
    assert {
        entity_id: change.partition_position
        for entity_id, change in delete_result.project_changes.items()
    } == {7: 11, 8: 12}


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
                FakeExecuteResult(rows=[]),  # canonical lock check
                FakeExecuteResult(rows=[]),  # incoming relation snapshot
                FakeExecuteResult(
                    rows=[deleted_entity_row(7, file_path="notes/example.md")]
                ),  # guarded entity delete
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_values=[]),  # vector rows
                FakeExecuteResult(scalar_value=1),  # journal position claim
                FakeExecuteResult(),  # journal insert
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
        project_external_id="project-123",
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
    assert delete_result.deleted_entity_ids == frozenset({7})
    assert delete_result.relation_cleanup_entity_ids == frozenset()
    change = delete_result.project_changes[7]
    assert (
        change.project_id,
        change.project_external_id,
        change.partition_position,
        change.note_external_id,
        change.permalink,
        change.title,
        change.file_path,
        change.operation,
        change.source,
        change.previous_file_path,
    ) == (
        3,
        "project-123",
        1,
        "note-external-7",
        "notes/example",
        "Note 7",
        "notes/example.md",
        RuntimeProjectNoteOperation.deleted,
        ACCEPTED_DIRECTORY_DELETE_SOURCE,
        None,
    )
    assert len(fake_session.queries) == 11
    assert "FOR UPDATE" not in str(fake_session.queries[1][0])
    assert "ORDER BY note_content.entity_id" in str(fake_session.queries[2][0])
    assert "entity.file_path LIKE" in str(fake_session.queries[3][0])
    guarded_entity_delete = str(fake_session.queries[6][0])
    assert "entity.id IN" in guarded_entity_delete
    assert "entity.project_id" in guarded_entity_delete
    assert "entity.file_path LIKE" in guarded_entity_delete
    assert "NOT (EXISTS" in guarded_entity_delete
    assert "RETURNING entity.id, entity.external_id" in guarded_entity_delete
    # Journal writes come last, after search and vector cleanup.
    assert "partition_position=(project.partition_position +" in str(fake_session.queries[9][0])
    assert "INSERT INTO accepted_project_note_change" in str(fake_session.queries[10][0])


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
                FakeExecuteResult(rows=[]),  # canonical lock check
                FakeExecuteResult(rows=[]),  # incoming relation snapshot
                FakeExecuteResult(
                    rows=[deleted_entity_row(7), deleted_entity_row(8)]
                ),  # guarded entity delete
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_value=2),  # journal position claim
                FakeExecuteResult(),  # journal insert
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
        project_external_id="project-123",
        directory="notes",
        entity_ids=[7, 8],
    )

    # Projection cleanup uses only ids returned by the guarded Entity mutation.
    assert vector_calls == [(session, 3, (7, 8), 6)]
    statements = [str(query) for query, _ in fake_session.queries]
    assert "ORDER BY note_content.entity_id" in statements[0]
    assert "DELETE FROM entity" in statements[4]
    assert "DELETE FROM search_index" in statements[5]


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
            project_external_id=test_project.external_id,
            directory="notes",
            entity_ids=[snapshot.entity_id for snapshot in snapshots],
        )

    assert delete_result == DirectoryEntityDeleteResult()
    assert await _partition_head(session_maker, test_project.id) == 0
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
            project_external_id=test_project.external_id,
            directory="notes",
            entity_ids=[target_id],
        )

    assert relation_resolved is True
    assert delete_result == DirectoryEntityDeleteResult()
    # A note the guarded delete leaves in place gets no journal row or position.
    assert await _partition_head(session_maker, test_project.id) == 0
    assert await _journal_rows(session_maker, test_project.id) == []
    async with db.scoped_session(session_maker) as session:
        target = await session.get(Entity, target_id)
        note_content = await session.get(NoteContent, target_id)
        relation = await session.get(Relation, relation_id)
    assert target is not None
    assert note_content is not None
    assert relation is not None
    assert relation.to_id == target_id


async def _seed_directory_entity(
    session: AsyncSession,
    *,
    project_id: int,
    file_path: str,
    content_type: str = "text/markdown",
    checksum: str | None = None,
    db_version: int | None = 1,
) -> Entity:
    """Insert one entity, plus accepted note content for Markdown notes."""
    now = datetime.now(tz=UTC)
    is_markdown = content_type == "text/markdown"
    entity = Entity(
        project_id=project_id,
        title=file_path.rsplit("/", 1)[-1].removesuffix(".md"),
        note_type="note" if is_markdown else "file",
        permalink=file_path.removesuffix(".md") if is_markdown else None,
        file_path=file_path,
        content_type=content_type,
        checksum=checksum,
        created_at=now,
        updated_at=now,
    )
    session.add(entity)
    await session.flush()
    if is_markdown and db_version is not None:
        session.add(
            NoteContent(
                entity_id=entity.id,
                project_id=project_id,
                external_id=entity.external_id,
                file_path=file_path,
                markdown_content=f"# {entity.title}\n",
                db_version=db_version,
                db_checksum=f"db-checksum-{entity.id}-{db_version}",
                file_write_status="synced",
            )
        )
        await session.flush()
    return entity


async def _set_partition_head(session_maker, project_id: int, position: int) -> None:
    async with db.scoped_session(session_maker) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.partition_position = position


async def _partition_head(session_maker, project_id: int) -> int:
    async with db.scoped_session(session_maker) as session:
        head = await ProjectRepository().get_partition_position(session, project_id)
    assert head is not None
    return head


async def _journal_rows(session_maker, project_id: int) -> list[AcceptedProjectNoteChange]:
    async with db.scoped_session(session_maker) as session:
        return list(await ProjectRepository().list_accepted_note_changes(session, project_id))


@pytest.mark.asyncio
async def test_directory_delete_journals_each_markdown_note_and_carries_rows_to_cleanup(
    session_maker,
    test_project,
) -> None:
    """A folder delete writes one `deleted` row per Markdown note it removes.

    Rows sit at consecutive positions in file-path order, carry the note's last
    accepted revision, skip non-Markdown files, and ride on each file's cleanup
    request. A queued cleanup settles its own row, so none is settled here.
    """
    await _set_partition_head(session_maker, test_project.id, 5)
    async with db.scoped_session(session_maker) as session:
        # Inserted out of path order, so the test proves rows follow file paths.
        second = await _seed_directory_entity(
            session, project_id=test_project.id, file_path="notes/b.md", db_version=4
        )
        first = await _seed_directory_entity(
            session, project_id=test_project.id, file_path="notes/a.md", db_version=2
        )
        image = await _seed_directory_entity(
            session,
            project_id=test_project.id,
            file_path="notes/diagram.png",
            content_type="image/png",
        )
        outside = await _seed_directory_entity(
            session, project_id=test_project.id, file_path="other/keep.md"
        )
        notes = {
            first.id: (first.external_id, 2, f"db-checksum-{first.id}-2"),
            second.id: (second.external_id, 4, f"db-checksum-{second.id}-4"),
        }
        image_id = image.id
        outside_id = outside.id

    enqueuer = FakeDirectoryFileDeleteEnqueuer()
    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=enqueuer,
        ),
    )
    result = await service.delete_directory(
        project_external_id=test_project.external_id,
        directory="notes",
    )

    assert sorted(result.deleted_files) == ["notes/a.md", "notes/b.md", "notes/diagram.png"]
    assert await _partition_head(session_maker, test_project.id) == 7
    rows = await _journal_rows(session_maker, test_project.id)
    assert [(row.partition_position, row.file_path) for row in rows] == [
        (6, "notes/a.md"),
        (7, "notes/b.md"),
    ]
    for row in rows:
        external_id, db_version, db_checksum = notes[row.entity_id]
        assert row.project_external_id == test_project.external_id
        assert row.note_external_id == external_id
        assert row.permalink == row.file_path.removesuffix(".md")
        assert row.title == row.file_path.rsplit("/", 1)[-1].removesuffix(".md")
        assert row.operation == RuntimeProjectNoteOperation.deleted.value
        assert row.source == ACCEPTED_DIRECTORY_DELETE_SOURCE
        assert row.previous_file_path is None
        assert (row.db_version, row.db_checksum) == (db_version, db_checksum)
        assert row.actor_kind is None
        # Queued cleanup owns settlement; the service must not pre-empt it.
        assert row.materialized_at is None
    assert {row.entity_id for row in rows}.isdisjoint({image_id, outside_id})

    project_change_by_path = {
        request.file_path: request.project_change for request in enqueuer.requests
    }
    assert project_change_by_path["notes/diagram.png"] is None
    for row in rows:
        change = project_change_by_path[row.file_path]
        assert change is not None
        assert (change.partition_position, change.entity_id, change.source) == (
            row.partition_position,
            row.entity_id,
            ACCEPTED_DIRECTORY_DELETE_SOURCE,
        )


@pytest.mark.asyncio
async def test_directory_delete_journal_rolls_back_with_failed_transaction(
    session_maker,
    test_project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the entity delete keeps the notes, the journal and the head unchanged."""
    await _set_partition_head(session_maker, test_project.id, 3)
    async with db.scoped_session(session_maker) as session:
        note = await _seed_directory_entity(
            session, project_id=test_project.id, file_path="notes/a.md"
        )
        note_id = note.id

    async def failing_record(self, session, changes) -> None:
        raise RuntimeError("journal insert failed")

    monkeypatch.setattr(ProjectRepository, "record_accepted_note_changes", failing_record)
    enqueuer = FakeDirectoryFileDeleteEnqueuer()
    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=enqueuer,
        ),
    )
    with pytest.raises(RuntimeError, match="journal insert failed"):
        await service.delete_directory(
            project_external_id=test_project.external_id,
            directory="notes",
        )

    async with db.scoped_session(session_maker) as session:
        assert await session.get(Entity, note_id) is not None
        assert await session.get(NoteContent, note_id) is not None
    assert await _partition_head(session_maker, test_project.id) == 3
    assert await _journal_rows(session_maker, test_project.id) == []
    assert enqueuer.requests == []


@pytest.mark.asyncio
async def test_directory_delete_claims_no_position_without_deleted_markdown(
    session_maker,
    test_project,
) -> None:
    """An empty folder, or one holding only non-Markdown files, leaves the journal alone."""
    async with db.scoped_session(session_maker) as session:
        await _seed_directory_entity(
            session,
            project_id=test_project.id,
            file_path="images/photo.png",
            content_type="image/png",
        )
    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=FakeDirectoryFileDeleteEnqueuer(),
        ),
    )

    empty = await service.delete_directory(
        project_external_id=test_project.external_id,
        directory="nothing-here",
    )
    images = await service.delete_directory(
        project_external_id=test_project.external_id,
        directory="images",
    )

    assert empty == DirectoryDeleteAcceptedResult.complete()
    assert images.deleted_files == ("images/photo.png",)
    assert await _partition_head(session_maker, test_project.id) == 0
    assert await _journal_rows(session_maker, test_project.id) == []


@pytest.mark.asyncio
async def test_local_directory_delete_settles_every_row_whatever_the_cleanup_outcome(
    session_maker,
    test_project,
    file_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local cleanup runs inline, so the service settles each row itself.

    Deleted, already-missing, changed-on-disk (skipped) and failed cleanups all
    settle: the entity delete is final, and one unsettled row would defer every
    later wiki projection for the project (GitHub #1625).
    """
    checksums: dict[str, str] = {}
    for path in ("notes/deleted.md", "notes/changed.md", "notes/broken.md"):
        await file_service.write_file(path, f"# {path}\n")
        checksums[path] = await file_service.compute_checksum(path)
    async with db.scoped_session(session_maker) as session:
        await _seed_directory_entity(
            session,
            project_id=test_project.id,
            file_path="notes/deleted.md",
            checksum=checksums["notes/deleted.md"],
        )
        await _seed_directory_entity(
            session,
            project_id=test_project.id,
            file_path="notes/missing.md",
            checksum="checksum-of-a-file-that-is-gone",
        )
        await _seed_directory_entity(
            session,
            project_id=test_project.id,
            file_path="notes/changed.md",
            checksum=checksums["notes/changed.md"],
        )
        await _seed_directory_entity(
            session,
            project_id=test_project.id,
            file_path="notes/broken.md",
            checksum=checksums["notes/broken.md"],
        )
    # Edited on disk after the last index pass, so the guarded cleanup skips it.
    await file_service.write_file("notes/changed.md", "# edited elsewhere\n")

    original_delete_file = file_service.delete_file

    async def delete_file_failing_for_broken(path) -> None:
        if str(path) == "notes/broken.md":
            raise OSError("permission denied: notes/broken.md")
        await original_delete_file(path)

    monkeypatch.setattr(file_service, "delete_file", delete_file_failing_for_broken)

    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=LocalDirectoryFileDeleteEnqueuer(file_service=file_service),
        ),
    )
    result = await service.delete_directory(
        project_external_id=test_project.external_id,
        directory="notes",
    )

    assert {failure.file_path for failure in result.failed_files} == {
        "notes/changed.md",
        "notes/broken.md",
    }
    assert not await file_service.exists("notes/deleted.md")
    rows = await _journal_rows(session_maker, test_project.id)
    assert sorted(row.file_path for row in rows) == [
        "notes/broken.md",
        "notes/changed.md",
        "notes/deleted.md",
        "notes/missing.md",
    ]
    assert [row.file_path for row in rows if row.materialized_at is None] == []

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    assert inspection.plan.result.pending_materialization == ()


@pytest.mark.asyncio
async def test_run_directory_delete_settles_inline_cleanup_rows(
    session_maker,
    test_project,
) -> None:
    """The single-session runner settles rows for cleanup that ran inline, like the service."""

    class InlineMissingEnqueuer:
        async def enqueue_directory_file_delete(
            self,
            request: RuntimeNoteFileDeleteJobRequest,
        ) -> RuntimeFileDeleteResult:
            return RuntimeFileDeleteResult.already_absent(
                entity_id=request.entity_id,
                file_path=request.file_path,
            )

    async with db.scoped_session(session_maker) as session:
        await _seed_directory_entity(session, project_id=test_project.id, file_path="notes/a.md")
    async with db.scoped_session(session_maker) as session:
        result = await run_directory_delete(
            session,
            request=DirectoryDeleteAcceptanceRequest(
                project_external_id=test_project.external_id,
                directory="notes",
            ),
            runtime=DirectoryDeleteRuntime(
                store=RepositoryDirectoryDeleteAcceptanceStore(),
                file_delete_enqueuer=InlineMissingEnqueuer(),
            ),
        )

    assert [change.file_path for change in result.project_changes_to_settle] == ["notes/a.md"]
    rows = await _journal_rows(session_maker, test_project.id)
    assert [(row.file_path, row.materialized_at is not None) for row in rows] == [
        ("notes/a.md", True)
    ]
