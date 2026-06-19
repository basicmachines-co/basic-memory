"""Portable orchestration for project cleanup jobs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.runtime import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectDeleteResult,
    RuntimeProjectFileSnapshot,
    plan_note_file_delete_job_request,
)


@dataclass(frozen=True, slots=True)
class ProjectDeletePreflightResult:
    """Project state before a background hard-delete can proceed."""

    terminal_result: RuntimeProjectDeleteResult | None = None
    file_snapshots: tuple[RuntimeProjectFileSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.terminal_result is not None and self.file_snapshots:
            raise ValueError("terminal project delete preflight cannot carry file snapshots")

    @classmethod
    def terminal(cls, result: RuntimeProjectDeleteResult) -> Self:
        """Return a preflight result that finishes without file or project deletes."""
        return cls(terminal_result=result)

    @classmethod
    def ready(cls, file_snapshots: Sequence[RuntimeProjectFileSnapshot]) -> Self:
        """Return a preflight result ready to clean files and hard-delete the project."""
        return cls(file_snapshots=tuple(file_snapshots))


class ProjectDeletePreflightProvider(Protocol):
    """Capability that checks project state and captures cleanup file snapshots."""

    async def prepare_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectDeletePreflightResult: ...


class ProjectDeleteFileDeleter(Protocol):
    """Capability that deletes one materialized file owned by project cleanup."""

    async def delete_project_file(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult: ...


class ProjectHardDeleter(Protocol):
    """Capability that hard-deletes the project row after file cleanup."""

    async def hard_delete_project(self, request: RuntimeProjectDeleteJobRequest) -> bool: ...


async def run_project_delete(
    request: RuntimeProjectDeleteJobRequest,
    *,
    preflight: ProjectDeletePreflightProvider,
    file_deleter: ProjectDeleteFileDeleter,
    hard_deleter: ProjectHardDeleter,
) -> RuntimeProjectDeleteResult:
    """Run one project cleanup request through file cleanup then hard delete."""
    preflight_result = await preflight.prepare_project_delete(request)
    if preflight_result.terminal_result is not None:
        return preflight_result.terminal_result

    file_results: list[RuntimeFileDeleteResult] = []
    for file_snapshot in preflight_result.file_snapshots:
        file_results.append(
            await file_deleter.delete_project_file(
                plan_note_file_delete_job_request(
                    tenant_id=request.tenant_id,
                    file_delete=file_snapshot.to_pending_note_file_delete(
                        project_id=request.project_id
                    ),
                )
            )
        )

    deleted_project = await hard_deleter.hard_delete_project(request)
    if deleted_project:
        status = RuntimeDeleteStatus.deleted
        reason = f"project deleted: {request.project_id}"
    else:
        status = RuntimeDeleteStatus.missing
        reason = f"project already absent: {request.project_id}"

    return RuntimeProjectDeleteResult.from_file_results(
        project_id=request.project_id,
        project_external_id=request.project_external_id,
        status=status,
        deleted_project=deleted_project,
        file_results=file_results,
        reason=reason,
    )
