"""Local project-wide indexing adapters for the core coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.filesystem import local_relative_path_is_filtered
from basic_memory.index.local_dependencies import (
    LocalIndexProjectDependencies,
    LocalIndexProjectDependencyProvider,
    build_local_index_project_dependencies,
)
from basic_memory.index.local_runtime import (
    LOCAL_EVENT_INDEX_TENANT_ID,
    LocalStorageFileMetadataSource,
)
from basic_memory.indexing import (
    ChangeDetector,
    FileIndexChecker,
    IndexFileExecutor,
    IndexFileJobResult,
    IndexFileMaterializedNoteSource,
    IndexFileMetadataSource,
    IndexFileRunnerChecker,
    IndexFileRuntimeRequest,
    RepositoryCurrentMaterializedNoteSource,
    RepositoryIndexedFileChecksumSource,
    RepositoryProjectIndexMaintenanceStore,
    RepositoryRelationResolutionRuntime,
    StorageCurrentFileChecksumSource,
    ProjectIndexBatchEnqueuer,
    ProjectIndexChangeDetector,
    ProjectIndexCompletion,
    ProjectIndexCoordinatorResult,
    ProjectIndexFanoutFailureRecorder,
    ProjectIndexMaintenanceRunner,
    ProjectIndexObservedFileSource,
    ProjectIndexWorkflowRequest,
    ProjectIndexWorkflowStarter,
    ProjectIndexRelationResolutionContext,
    RelationResolutionRuntime,
    StoreProjectIndexMaintenanceRunner,
    resolve_project_index_completion_relations,
    run_project_index_coordinator,
    run_index_file,
)
from basic_memory.models import Project
from basic_memory.runtime import (
    ProjectRuntimeReference,
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


class LocalProjectIndexProjectRepository(Protocol):
    """Project lookup capability needed by local project-index runners."""

    async def get_by_id(self, session: AsyncSession, project_id: int) -> Project | None: ...


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
    change_detector: ProjectIndexChangeDetector
    maintenance_runner: ProjectIndexMaintenanceRunner
    batch_enqueuer: ProjectIndexBatchEnqueuer
    workflow_starter: ProjectIndexWorkflowStarter = NoopProjectIndexWorkflowStarter()
    fanout_failure_recorder: ProjectIndexFanoutFailureRecorder = (
        NoopProjectIndexFanoutFailureRecorder()
    )
    completion_relation_runtime: RelationResolutionRuntime | None = None
    batch_size: int = 100
    coordinator_job_id: RuntimeJobId | None = None


@dataclass(frozen=True, slots=True)
class LocalProjectIndexObservation:
    """Current local project files observed through the project-index adapter."""

    observed_files: tuple[RuntimeObservedIndexFile, ...]

    @property
    def total_files(self) -> int:
        return len(self.observed_files)


class LocalProjectIndexRuntimeProvider(Protocol):
    """Minimal factory shape needed to run a local project-index request."""

    tenant_id: TenantId

    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime: ...


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

    dependency_provider: LocalIndexProjectDependencyProvider = (
        build_local_index_project_dependencies
    )
    tenant_id: TenantId = LOCAL_EVENT_INDEX_TENANT_ID
    batch_size: int = 100

    async def dependencies_for_project(self, project: Project) -> LocalIndexProjectDependencies:
        return await self.dependency_provider(project)

    def runtime_from_dependencies(
        self,
        dependencies: LocalIndexProjectDependencies,
    ) -> LocalProjectIndexRuntime:
        metadata_source = LocalStorageFileMetadataSource(dependencies.file_service)
        checker = FileIndexChecker(
            indexed_checksum_source=RepositoryIndexedFileChecksumSource(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
            ),
            current_checksum_source=StorageCurrentFileChecksumSource(
                metadata_source=metadata_source,
            ),
        )
        file_runner = LocalProjectIndexFileRunner(
            checker=checker,
            metadata_source=metadata_source,
            materialized_note_source=RepositoryCurrentMaterializedNoteSource(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
            ),
            file_indexer=dependencies.file_indexer,
        )
        maintenance_store = RepositoryProjectIndexMaintenanceStore(
            session_maker=dependencies.session_maker,
            project_id=dependencies.project_id,
        )
        return LocalProjectIndexRuntime(
            observed_file_source=LocalProjectIndexObservedFileSource(
                dependencies.file_service,
            ),
            change_detector=ChangeDetector(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
            ),
            maintenance_runner=StoreProjectIndexMaintenanceRunner(
                move_store=maintenance_store,
                delete_store=maintenance_store,
            ),
            batch_enqueuer=InlineProjectIndexBatchEnqueuer(file_runner),
            completion_relation_runtime=RepositoryRelationResolutionRuntime(
                session_maker=dependencies.session_maker,
                relation_repository=dependencies.relation_repository,
                entity_repository=dependencies.entity_repository,
                link_resolver=dependencies.link_resolver,
                entity_indexer=dependencies.search_service,
            ),
            batch_size=self.batch_size,
        )

    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime:
        return self.runtime_from_dependencies(await self.dependencies_for_project(project))


async def run_local_project_index_for_project(
    project: Project,
    *,
    runtime_factory: LocalProjectIndexRuntimeProvider,
    force_full: bool = False,
) -> ProjectIndexCoordinatorResult:
    """Run local project indexing for one project through the core fanout runtime."""
    return await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            tenant_id=runtime_factory.tenant_id,
            project=ProjectRuntimeReference.from_project(project),
            workflow_id=uuid4(),
            force_full=force_full,
        ),
        runtime=await runtime_factory.runtime_for_project(project),
    )


@dataclass(frozen=True, slots=True)
class LocalProjectIndexRunner:
    """API/task-facing runner for local project observation and project-wide indexing."""

    project_repository: LocalProjectIndexProjectRepository
    session_maker: async_sessionmaker[AsyncSession]
    runtime_factory: LocalProjectIndexRuntimeFactory = LocalProjectIndexRuntimeFactory()

    async def _get_project(self, project_id: int) -> Project:
        async with db.scoped_session(self.session_maker) as session:
            project = await self.project_repository.get_by_id(session, project_id)
        if project is None:
            raise ValueError(f"Project with ID {project_id} not found")
        return project

    async def observe_project(self, project_id: int) -> LocalProjectIndexObservation:
        project = await self._get_project(project_id)
        dependencies = await self.runtime_factory.dependencies_for_project(project)
        runtime = self.runtime_factory.runtime_from_dependencies(dependencies)
        observed_files = await runtime.observed_file_source.list_observed_index_files()
        return LocalProjectIndexObservation(observed_files=observed_files)

    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult:
        project = await self._get_project(project_id)
        return await run_local_project_index_for_project(
            project,
            runtime_factory=self.runtime_factory,
            force_full=force_full,
        )


async def run_local_project_index(
    request: RuntimeProjectIndexJobRequest,
    *,
    runtime: LocalProjectIndexRuntime,
) -> ProjectIndexCoordinatorResult:
    """Run project-wide local indexing through the storage-neutral coordinator."""
    result = await run_project_index_coordinator(
        request,
        coordinator_job_id=runtime.coordinator_job_id,
        observed_file_source=runtime.observed_file_source,
        change_detector=runtime.change_detector,
        maintenance_runner=runtime.maintenance_runner,
        workflow_starter=runtime.workflow_starter,
        batch_enqueuer=runtime.batch_enqueuer,
        fanout_failure_recorder=runtime.fanout_failure_recorder,
        batch_size=runtime.batch_size,
    )
    if runtime.completion_relation_runtime is not None:
        await resolve_project_index_completion_relations(
            ProjectIndexRelationResolutionContext(
                tenant_id=request.tenant_id,
                project_id=request.project.project_id,
                project_path=request.project.project_path,
            ),
            runtime.completion_relation_runtime,
        )
    return result
