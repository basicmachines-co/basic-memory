"""Tests for portable directory-delete cleanup orchestration."""

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import basic_memory.indexing.directory_delete_runner as directory_delete_runner_module
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteAcceptanceRequest,
    DirectoryDeleteAcceptedResult,
    DirectoryDeleteFileFailure,
    DirectoryDeleteRejected,
    DirectoryDeleteRejectKind,
    DirectoryFileDeleteEnqueueError,
    RepositoryDirectoryDeleteAcceptanceStore,
    DirectoryDeleteRuntime,
    enqueue_directory_file_delete_jobs,
    normalize_directory_delete_path,
    run_directory_delete,
)
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
        entity_ids: Sequence[int],
    ) -> frozenset[int]:
        assert project_id == self.project_id
        self.deleted_entity_ids.append(tuple(entity_ids))
        return self.relation_cleanup_entity_ids


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
                FakeExecuteResult(scalar_values=[42, 99]),  # surviving relation sources
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_values=[]),  # vector rows
                FakeExecuteResult(),  # entity delete
            ]
        ),
    )
    store = RepositoryDirectoryDeleteAcceptanceStore()

    relation_cleanup_entity_ids = await store.delete_directory_entities(
        session,
        project_id=3,
        entity_ids=[7, 8],
    )

    assert relation_cleanup_entity_ids == frozenset({42, 99})


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
                FakeExecuteResult(scalar_values=[]),  # surviving relation sources
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(scalar_values=[]),  # vector rows
                FakeExecuteResult(),  # entity delete
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
    relation_cleanup_entity_ids = await store.delete_directory_entities(
        session,
        project_id=3,
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
    assert relation_cleanup_entity_ids == frozenset()
    assert len(fake_session.queries) == 6


@pytest.mark.asyncio
async def test_repository_directory_delete_store_clears_vectors_before_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(
        AsyncSession,
        FakeExecuteSession(
            [
                FakeExecuteResult(scalar_values=[]),  # surviving relation sources
                FakeExecuteResult(),  # search_index delete
                FakeExecuteResult(),  # entity delete
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
        entity_ids=[7, 8],
    )

    # Vector rows are cleared after the relation-source select and search_index delete
    # (2 queries so far) but before the entity delete, so CASCADE cannot race the rows.
    assert vector_calls == [(session, 3, (7, 8), 2)]
    statements = [str(query) for query, _ in fake_session.queries]
    assert "DELETE FROM search_index" in statements[1]
    assert "DELETE FROM entity" in statements[2]
