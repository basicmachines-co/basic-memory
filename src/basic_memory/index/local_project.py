"""Local project-wide indexing adapters for the core coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.filesystem import local_relative_path_is_filtered
from basic_memory.index.local_runtime import (
    LOCAL_EVENT_INDEX_TENANT_ID,
    LocalEventIndexEntityRepository,
    LocalStorageFileMetadataSource,
)
from basic_memory.indexing import (
    CurrentMaterializedNoteEntityRepository,
    FileIndexChecker,
    IndexedFileChecksumRepository,
    IndexFileExecutor,
    IndexFileJobResult,
    IndexFileMaterializedNoteSource,
    IndexFileMetadataSource,
    IndexFileRunnerChecker,
    IndexFileRuntimeRequest,
    IndexMarkdownSyncService,
    OrphanCleanupLogger,
    OrphanEntityRepository,
    OrphanEntityCleanupResult,
    RepositoryCurrentMaterializedNoteSource,
    RepositoryIndexedFileChecksumSource,
    StorageCurrentFileChecksumSource,
    build_default_file_indexer,
    cleanup_orphan_entities,
    ProjectIndexBatchEnqueuer,
    ProjectIndexCompletion,
    ProjectIndexCoordinatorResult,
    ProjectIndexFanoutFailureRecorder,
    ProjectIndexObservedFileSource,
    ProjectIndexOrphanCleaner,
    ProjectIndexWorkflowRequest,
    ProjectIndexWorkflowStarter,
    OrphanSearchIndex,
    run_project_index_coordinator,
    run_index_file,
)
from basic_memory.models import Entity, Project
from basic_memory.runtime import (
    RuntimeJobId,
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
    TenantId,
    WorkflowId,
)
from basic_memory.services import FileService

type LocalProjectIndexIgnorePatterns = set[str]


class ProjectIndexFileRequestRunner(Protocol):
    """Capability that runs one typed file-index request inline."""

    async def run_index_file_request(
        self,
        request: IndexFileRuntimeRequest,
    ) -> IndexFileJobResult: ...


class LocalProjectIndexEntityRepository(
    LocalEventIndexEntityRepository,
    IndexedFileChecksumRepository,
    CurrentMaterializedNoteEntityRepository,
    OrphanEntityRepository[Entity],
    Protocol,
):
    """Entity repository capabilities needed by local project indexing."""


class LocalProjectIndexSyncService(IndexMarkdownSyncService, Protocol):
    """Sync-service capabilities needed to compose local project indexing."""

    @property
    def entity_repository(self) -> LocalProjectIndexEntityRepository: ...

    @property
    def file_service(self) -> FileService: ...

    @property
    def search_service(self) -> OrphanSearchIndex[Entity]: ...


type LocalProjectIndexSyncServiceFactory = Callable[
    [Project],
    Awaitable[LocalProjectIndexSyncService],
]


@dataclass(frozen=True, slots=True)
class LocalProjectIndexOrphanCleaner(ProjectIndexOrphanCleaner):
    """Clean stale local DB/search rows before local project-index fanout."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: LocalProjectIndexEntityRepository
    search_service: OrphanSearchIndex[Entity]
    logger: OrphanCleanupLogger | None = None

    async def cleanup_orphans(self, current_paths: set[str]) -> OrphanEntityCleanupResult:
        return await cleanup_orphan_entities(
            session_maker=self.session_maker,
            entity_repository=self.entity_repository,
            search_service=self.search_service,
            current_paths=current_paths,
            logger=self.logger,
        )


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


def project_index_file_requests_from_batch_request(
    request: RuntimeIndexFileBatchJobRequest,
) -> tuple[IndexFileRuntimeRequest, ...]:
    """Return per-file index requests represented by one project-index batch."""
    if request.observed_files:
        return tuple(
            index_file_request_from_observed_file(
                request,
                observed_file=observed_file,
            )
            for observed_file in request.observed_files
        )

    return tuple(
        index_file_request_from_path(
            request,
            file_path=file_path,
        )
        for file_path in request.file_paths
    )


def index_file_request_from_observed_file(
    request: RuntimeIndexFileBatchJobRequest,
    *,
    observed_file: RuntimeObservedIndexFile,
) -> IndexFileRuntimeRequest:
    """Build a file-index request from observed project-index storage metadata."""
    if observed_file.checksum is None:
        return index_file_request_from_path(request, file_path=observed_file.path)

    return IndexFileRuntimeRequest(
        tenant_id=request.tenant_id,
        project_id=request.project.project_id,
        project_external_id=request.project.project_external_id,
        project_name=request.project.project_name,
        project_path=request.project.project_path,
        file_path=observed_file.path,
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(
            etag=observed_file.checksum,
            size=observed_file.size,
        ),
        index_embeddings=request.index_embeddings,
        workflow_id=request.workflow_id,
    )


def index_file_request_from_path(
    request: RuntimeIndexFileBatchJobRequest,
    *,
    file_path: str,
) -> IndexFileRuntimeRequest:
    """Build a current-file index request when no observed storage metadata exists."""
    return IndexFileRuntimeRequest(
        tenant_id=request.tenant_id,
        project_id=request.project.project_id,
        project_external_id=request.project.project_external_id,
        project_name=request.project.project_name,
        project_path=request.project.project_path,
        file_path=file_path,
        mode=RuntimeStorageFileIndexMode.current_file,
        object_observation=None,
        index_embeddings=request.index_embeddings,
        workflow_id=request.workflow_id,
    )


@dataclass(frozen=True, slots=True)
class InlineProjectIndexBatchEnqueuer(ProjectIndexBatchEnqueuer):
    """Run project-index child batches immediately in the current process."""

    file_runner: ProjectIndexFileRequestRunner

    async def enqueue_index_file_batch(
        self,
        request: RuntimeIndexFileBatchJobRequest,
    ) -> None:
        for file_request in project_index_file_requests_from_batch_request(request):
            await self.file_runner.run_index_file_request(file_request)


@dataclass(frozen=True, slots=True)
class LocalProjectIndexFileRunner(ProjectIndexFileRequestRunner):
    """Run project-index child file requests through the core per-file runner."""

    checker: IndexFileRunnerChecker
    metadata_source: IndexFileMetadataSource
    materialized_note_source: IndexFileMaterializedNoteSource
    file_indexer: IndexFileExecutor

    async def run_index_file_request(
        self,
        request: IndexFileRuntimeRequest,
    ) -> IndexFileJobResult:
        return await run_index_file(
            request,
            checker=self.checker,
            metadata_source=self.metadata_source,
            materialized_note_source=self.materialized_note_source,
            file_indexer=self.file_indexer,
        )


@dataclass(frozen=True, slots=True)
class LocalProjectIndexRuntimeFactory:
    """Build project-wide local indexing runtime dependencies for one project."""

    sync_service_factory: LocalProjectIndexSyncServiceFactory
    tenant_id: TenantId = LOCAL_EVENT_INDEX_TENANT_ID
    batch_size: int = 100

    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime:
        sync_service = await self.sync_service_factory(project)
        metadata_source = LocalStorageFileMetadataSource(sync_service.file_service)
        checker = FileIndexChecker(
            indexed_checksum_source=RepositoryIndexedFileChecksumSource(
                session_maker=sync_service.session_maker,
                entity_repository=sync_service.entity_repository,
            ),
            current_checksum_source=StorageCurrentFileChecksumSource(
                load_metadata=metadata_source.load_current_file_metadata,
            ),
        )
        file_runner = LocalProjectIndexFileRunner(
            checker=checker,
            metadata_source=metadata_source,
            materialized_note_source=RepositoryCurrentMaterializedNoteSource(
                session_maker=sync_service.session_maker,
                entity_repository=sync_service.entity_repository,
            ),
            file_indexer=build_default_file_indexer(
                project_id=project.id,
                sync_service=sync_service,
            ),
        )
        return LocalProjectIndexRuntime(
            observed_file_source=LocalProjectIndexObservedFileSource(sync_service.file_service),
            orphan_cleaner=LocalProjectIndexOrphanCleaner(
                session_maker=sync_service.session_maker,
                entity_repository=sync_service.entity_repository,
                search_service=sync_service.search_service,
            ),
            batch_enqueuer=InlineProjectIndexBatchEnqueuer(file_runner),
            batch_size=self.batch_size,
        )


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
