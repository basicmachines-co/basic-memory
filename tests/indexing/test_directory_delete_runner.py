"""Tests for portable directory-delete cleanup orchestration."""

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import basic_memory.indexing.directory_delete_runner as directory_delete_runner_module
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteAcceptanceRequest,
    DirectoryDeleteAcceptedResult,
    DirectoryDeleteRejected,
    DirectoryDeleteRejectKind,
    RepositoryDirectoryDeleteAcceptanceStore,
    DirectoryDeleteRuntime,
    enqueue_directory_file_delete_jobs,
    normalize_directory_delete_path,
    run_directory_delete,
)
from basic_memory.runtime import (
    RuntimeDirectoryFileSnapshot,
    RuntimeNoteFileDeleteJobRequest,
)


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
    ) -> None:
        self.project_id = project_id
        self.files = files or []
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
    ) -> None:
        assert project_id == self.project_id
        self.deleted_entity_ids.append(tuple(entity_ids))


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
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    enqueuer = FakeDirectoryFileDeleteEnqueuer()

    await enqueue_directory_file_delete_jobs(
        tenant_id=tenant_id,
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
            tenant_id=tenant_id,
            project_id=3,
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="note-sha",
        ),
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=tenant_id,
            project_id=3,
            entity_id=8,
            file_path="notes/legacy.md",
            file_checksum=None,
        ),
    ]


@pytest.mark.asyncio
async def test_run_directory_delete_accepts_rows_and_queues_cleanup() -> None:
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    files = [
        directory_snapshot(entity_id=7, file_path="notes/a.md"),
        directory_snapshot(entity_id=8, file_path="notes/b.md", file_checksum=None),
    ]
    store = FakeDirectoryDeleteStore(project_id=3, files=files)
    enqueuer = FakeDirectoryFileDeleteEnqueuer()

    result = await run_directory_delete(
        AsyncSession(),
        request=DirectoryDeleteAcceptanceRequest(
            tenant_id=tenant_id,
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
    assert enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=tenant_id,
            project_id=3,
            entity_id=7,
            file_path="notes/a.md",
            file_checksum="note-sha",
        ),
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=tenant_id,
            project_id=3,
            entity_id=8,
            file_path="notes/b.md",
            file_checksum=None,
        ),
    ]


@pytest.mark.asyncio
async def test_run_directory_delete_rejects_unknown_project() -> None:
    with pytest.raises(DirectoryDeleteRejected) as exc_info:
        await run_directory_delete(
            AsyncSession(),
            request=DirectoryDeleteAcceptanceRequest(
                tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
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
                FakeExecuteResult(),
                FakeExecuteResult(scalar_values=[]),
                FakeExecuteResult(),
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
    await store.delete_directory_entities(
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
    assert len(fake_session.queries) == 5


@pytest.mark.asyncio
async def test_repository_directory_delete_store_clears_vectors_before_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(
        AsyncSession,
        FakeExecuteSession(
            [
                FakeExecuteResult(),
                FakeExecuteResult(),
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

    assert vector_calls == [(session, 3, (7, 8), 1)]
    statements = [str(query) for query, _ in fake_session.queries]
    assert "DELETE FROM search_index" in statements[0]
    assert "DELETE FROM entity" in statements[1]
