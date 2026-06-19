"""Tests for portable project-delete cleanup orchestration."""

from uuid import UUID

import pytest

from basic_memory.indexing.project_delete_runner import (
    ProjectDeletePreflightResult,
    run_project_delete,
)
from basic_memory.runtime import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectDeleteResult,
    RuntimeProjectFileSnapshot,
)


class FakeProjectDeletePreflight:
    def __init__(self, result: ProjectDeletePreflightResult) -> None:
        self.result = result
        self.requests: list[RuntimeProjectDeleteJobRequest] = []

    async def prepare_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectDeletePreflightResult:
        self.requests.append(request)
        return self.result


class FakeProjectFileDeleter:
    def __init__(self, results: list[RuntimeFileDeleteResult]) -> None:
        self.results = results
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def delete_project_file(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult:
        self.requests.append(request)
        return self.results.pop(0)


class FakeProjectHardDeleter:
    def __init__(self, *, deleted: bool) -> None:
        self.deleted = deleted
        self.requests: list[RuntimeProjectDeleteJobRequest] = []

    async def hard_delete_project(self, request: RuntimeProjectDeleteJobRequest) -> bool:
        self.requests.append(request)
        return self.deleted


def project_delete_request() -> RuntimeProjectDeleteJobRequest:
    return RuntimeProjectDeleteJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="basic-memory",
        delete_notes=True,
    )


def project_file_snapshot(
    *,
    entity_id: int,
    file_path: str,
    file_checksum: str | None,
) -> RuntimeProjectFileSnapshot:
    return RuntimeProjectFileSnapshot(
        entity_id=entity_id,
        file_path=file_path,
        file_checksum=file_checksum,
    )


@pytest.mark.asyncio
async def test_run_project_delete_deletes_files_then_hard_deletes_project() -> None:
    request = project_delete_request()
    file_deleter = FakeProjectFileDeleter(
        [
            RuntimeFileDeleteResult.deleted(entity_id=42, file_path="notes/a.md"),
            RuntimeFileDeleteResult.changed_before_delete(
                entity_id=43,
                file_path="notes/b.md",
            ),
        ]
    )
    hard_deleter = FakeProjectHardDeleter(deleted=True)

    result = await run_project_delete(
        request,
        preflight=FakeProjectDeletePreflight(
            ProjectDeletePreflightResult.ready(
                [
                    project_file_snapshot(
                        entity_id=42,
                        file_path="notes/a.md",
                        file_checksum="file-sum-a",
                    ),
                    project_file_snapshot(
                        entity_id=43,
                        file_path="notes/b.md",
                        file_checksum="file-sum-b",
                    ),
                ]
            )
        ),
        file_deleter=file_deleter,
        hard_deleter=hard_deleter,
    )

    assert result == RuntimeProjectDeleteResult(
        project_id=101,
        project_external_id="project-main",
        status=RuntimeDeleteStatus.deleted,
        deleted_project=True,
        deleted_files=1,
        skipped_files=1,
        missing_files=0,
        reason="project deleted: 101",
    )
    assert file_deleter.requests == [
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=request.tenant_id,
            project_id=101,
            entity_id=42,
            file_path="notes/a.md",
            file_checksum="file-sum-a",
        ),
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=request.tenant_id,
            project_id=101,
            entity_id=43,
            file_path="notes/b.md",
            file_checksum="file-sum-b",
        ),
    ]
    assert hard_deleter.requests == [request]


@pytest.mark.asyncio
async def test_run_project_delete_returns_terminal_preflight_result() -> None:
    request = project_delete_request()
    terminal_result = RuntimeProjectDeleteResult(
        project_id=101,
        project_external_id="project-main",
        status=RuntimeDeleteStatus.skipped,
        deleted_project=False,
        deleted_files=0,
        skipped_files=0,
        missing_files=0,
        reason="project is active: 101",
    )
    file_deleter = FakeProjectFileDeleter([])
    hard_deleter = FakeProjectHardDeleter(deleted=True)

    result = await run_project_delete(
        request,
        preflight=FakeProjectDeletePreflight(
            ProjectDeletePreflightResult.terminal(terminal_result)
        ),
        file_deleter=file_deleter,
        hard_deleter=hard_deleter,
    )

    assert result == terminal_result
    assert file_deleter.requests == []
    assert hard_deleter.requests == []


@pytest.mark.asyncio
async def test_run_project_delete_preserves_file_counts_when_project_disappears() -> None:
    request = project_delete_request()
    file_deleter = FakeProjectFileDeleter(
        [
            RuntimeFileDeleteResult.already_absent(
                entity_id=42,
                file_path="notes/a.md",
            )
        ]
    )

    result = await run_project_delete(
        request,
        preflight=FakeProjectDeletePreflight(
            ProjectDeletePreflightResult.ready(
                [
                    project_file_snapshot(
                        entity_id=42,
                        file_path="notes/a.md",
                        file_checksum="file-sum",
                    )
                ]
            )
        ),
        file_deleter=file_deleter,
        hard_deleter=FakeProjectHardDeleter(deleted=False),
    )

    assert result == RuntimeProjectDeleteResult(
        project_id=101,
        project_external_id="project-main",
        status=RuntimeDeleteStatus.missing,
        deleted_project=False,
        deleted_files=0,
        skipped_files=0,
        missing_files=1,
        reason="project already absent: 101",
    )
