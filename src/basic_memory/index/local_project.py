"""Local project-wide indexing adapters for the core coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.filesystem import local_relative_path_is_filtered
from basic_memory.indexing import (
    ProjectIndexBatchEnqueuer,
    ProjectIndexCompletion,
    ProjectIndexCoordinatorResult,
    ProjectIndexFanoutFailureRecorder,
    ProjectIndexObservedFileSource,
    ProjectIndexOrphanCleaner,
    ProjectIndexWorkflowRequest,
    ProjectIndexWorkflowStarter,
    run_project_index_coordinator,
)
from basic_memory.runtime import (
    RuntimeJobId,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
    WorkflowId,
)
from basic_memory.services import FileService

type LocalProjectIndexIgnorePatterns = set[str]


def local_project_index_file_paths(
    project_root: Path,
    *,
    ignore_patterns: LocalProjectIndexIgnorePatterns | None = None,
) -> tuple[str, ...]:
    """Return sorted project-relative files eligible for local project indexing."""
    project_root = project_root.expanduser().resolve()
    active_ignore_patterns = (
        ignore_patterns if ignore_patterns is not None else load_gitignore_patterns(project_root)
    )
    file_paths: list[str] = []

    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(project_root).as_posix()
        if local_relative_path_is_filtered(relative_path):
            continue
        if should_ignore_path(path, project_root, active_ignore_patterns):
            continue
        file_paths.append(relative_path)

    return tuple(sorted(file_paths))


@dataclass(frozen=True, slots=True)
class LocalProjectIndexObservedFileSource(ProjectIndexObservedFileSource):
    """Observe local project files as project-index fanout targets."""

    file_service: FileService
    ignore_patterns: LocalProjectIndexIgnorePatterns | None = None

    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        file_paths = await asyncio.to_thread(
            local_project_index_file_paths,
            self.file_service.base_path,
            ignore_patterns=self.ignore_patterns,
        )
        observed_files: list[RuntimeObservedIndexFile] = []
        for file_path in file_paths:
            metadata = await self.file_service.get_file_metadata(file_path)
            observed_files.append(
                RuntimeObservedIndexFile(
                    path=file_path,
                    checksum=await self.file_service.compute_checksum(file_path),
                    size=metadata.size,
                )
            )
        return tuple(observed_files)


@dataclass(frozen=True, slots=True)
class NoopProjectIndexWorkflowStarter(ProjectIndexWorkflowStarter):
    """Local workflow starter for runtimes that do not persist progress rows."""

    async def start_project_index_workflow(
        self,
        request: ProjectIndexWorkflowRequest,
        *,
        total_files: int,
        batch_count: int,
        batch_size: int,
        coordinator_job_id: RuntimeJobId | None,
    ) -> ProjectIndexCompletion | None:
        return None


@dataclass(frozen=True, slots=True)
class NoopProjectIndexFanoutFailureRecorder(ProjectIndexFanoutFailureRecorder):
    """Local fanout failure recorder for runtimes without workflow persistence."""

    async def record_project_index_fanout_failure(
        self,
        *,
        workflow_id: WorkflowId,
        error_message: str,
        progress: str,
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class LocalProjectIndexRuntime:
    """Dependencies for running project-wide local indexing through core fanout."""

    observed_file_source: ProjectIndexObservedFileSource
    orphan_cleaner: ProjectIndexOrphanCleaner
    batch_enqueuer: ProjectIndexBatchEnqueuer
    workflow_starter: ProjectIndexWorkflowStarter = NoopProjectIndexWorkflowStarter()
    fanout_failure_recorder: ProjectIndexFanoutFailureRecorder = (
        NoopProjectIndexFanoutFailureRecorder()
    )
    batch_size: int = 100
    coordinator_job_id: RuntimeJobId | None = None


async def run_local_project_index(
    request: RuntimeProjectIndexJobRequest,
    *,
    runtime: LocalProjectIndexRuntime,
) -> ProjectIndexCoordinatorResult:
    """Run project-wide local indexing through the storage-neutral coordinator."""
    return await run_project_index_coordinator(
        request,
        coordinator_job_id=runtime.coordinator_job_id,
        observed_file_source=runtime.observed_file_source,
        orphan_cleaner=runtime.orphan_cleaner,
        workflow_starter=runtime.workflow_starter,
        batch_enqueuer=runtime.batch_enqueuer,
        fanout_failure_recorder=runtime.fanout_failure_recorder,
        batch_size=runtime.batch_size,
    )
