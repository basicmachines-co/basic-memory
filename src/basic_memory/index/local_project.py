"""Local project-wide indexing adapters for the core coordinator."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.file_utils import FileError, compute_checksum
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.index.filesystem import local_relative_path_is_filtered
from basic_memory.index.local_dependencies import (
    DefaultLocalIndexProjectDependencyProvider,
    LocalIndexProjectDependencies,
    LocalIndexProjectDependencyProvider,
)
from basic_memory.index.local_moves import LocalProjectIndexMoveContentUpdater
from basic_memory.index.local_runtime import LocalStorageFileMetadataSource
from basic_memory.indexing.change_detector import ChangeDetector
from basic_memory.indexing.embedding_index_planning import EmbeddingBatchVectorSync
from basic_memory.indexing.file_batch_runner import (
    IndexFileBatchChecker,
    IndexFileBatchContentClassifier,
    IndexFileBatchIndexer,
    IndexFileBatchReadOutcome,
    IndexFileBatchReadResult,
    IndexFileBatchReader,
    read_current_index_files,
    run_index_file_batch,
)
from basic_memory.indexing.file_index_checking import (
    FileIndexChecker,
    RepositoryIndexedFileChecksumSource,
    StorageCurrentFileChecksumSource,
)
from basic_memory.indexing.models import (
    IndexFileBatchJobResult,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexInputFile,
)
from basic_memory.indexing.project_index_coordinator import (
    ProjectIndexBatchEnqueuer,
    ProjectIndexChangeDetector,
    ProjectIndexCoordinatorResult,
    ProjectIndexFanoutFailureRecorder,
    ProjectIndexObservedFileSource,
    ProjectIndexWorkflowStarter,
    run_project_index_coordinator,
)
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexMaintenanceRunner,
    ProjectIndexMovedEntitySearchRefresher,
    RepositoryProjectIndexMaintenanceStore,
    RepositoryProjectIndexMovedEntitySearchRefresher,
    StoreProjectIndexMaintenanceRunner,
)
from basic_memory.indexing.relation_resolution import (
    ProjectIndexRelationResolutionContext,
    RelationResolutionRuntime,
    RepositoryRelationResolutionRuntime,
    resolve_project_index_completion_relations,
)
from basic_memory.models import Project
from basic_memory.runtime.jobs import (
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobId,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
)
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import RuntimeFilePath
from basic_memory.services import FileService
from basic_memory.services.exceptions import FileOperationError

type LocalProjectIndexIgnorePatterns = set[str]


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

    # os.walk lets us prune ignored/hidden directories *before* descending —
    # rglob("*") would stat every file inside node_modules/.venv/.git first — and
    # does not follow symlinked directories (followlinks=False). Symlinked files
    # are skipped explicitly so the batch reader never reads outside the project
    # boundary. This restores the pre-refactor scanner's no-follow + dir-pruning.
    def _scan_error(error: OSError) -> None:
        # Abort only if the project root itself is unreadable (missing/unmounted):
        # returning an empty snapshot would make the coordinator treat every indexed
        # entity as deleted. A failure deeper in the tree just prunes that subtree.
        if error.filename is not None and Path(error.filename) == project_root:
            raise error
        logger.warning("Skipping unreadable directory during project scan", path=error.filename)

    walker = os.walk(project_root, followlinks=False, onerror=_scan_error)
    while True:
        try:
            dirpath, dirnames, filenames = next(walker)
        except StopIteration:
            break
        except OSError:
            # The root scan failed (onerror re-raised). Never return an empty,
            # delete-everything snapshot; files discovered before a deeper traversal
            # error are kept.
            if not file_paths:
                raise
            break

        root_path = Path(dirpath)
        # Prune in place so os.walk never descends into ignored/hidden directories.
        dirnames[:] = [
            name
            for name in dirnames
            if not name.startswith(".")
            and not should_ignore_path(root_path / name, project_root, active_ignore_patterns)
        ]

        for name in filenames:
            path = root_path / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
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
            try:
                metadata = await self.file_service.get_file_metadata(file_path)
                checksum = await self.file_service.compute_checksum(file_path)
            except (OSError, FileError, FileOperationError) as exc:
                # Trigger: a path the walk just listed fails stat/checksum
                # (transient permission or mount error, or deleted mid-scan).
                # Why: dropping it from the observed snapshot makes delete
                # reconciliation treat the file as removed and destroy its
                # entity and search rows even though the file still exists.
                # Outcome: only a confirmed disappearance falls out of the
                # snapshot; anything else is carried through with unknown
                # metadata so the batch planner re-checks it instead.
                if await self._observed_file_confirmed_missing(file_path):
                    continue
                logger.warning(
                    "Carrying unobservable local index file through change detection",
                    path=file_path,
                    error=str(exc),
                )
                observed_files.append(RuntimeObservedIndexFile(path=file_path))
                continue
            observed_files.append(
                RuntimeObservedIndexFile(
                    path=file_path,
                    checksum=checksum,
                    size=metadata.size,
                )
            )
        return tuple(observed_files)

    async def _observed_file_confirmed_missing(self, file_path: RuntimeFilePath) -> bool:
        """Return True only when storage positively confirms the file is gone."""
        try:
            return not await self.file_service.exists(file_path)
        except FileOperationError:
            # The existence probe itself failed; assume the file is present so
            # a read error can never escalate into destructive reconciliation.
            return False


@dataclass(frozen=True, slots=True)
class LocalProjectIndexRuntime:
    """Dependencies for running project-wide local indexing through core fanout."""

    observed_file_source: ProjectIndexObservedFileSource
    change_detector: ProjectIndexChangeDetector
    maintenance_runner: ProjectIndexMaintenanceRunner
    moved_entity_search_refresher: ProjectIndexMovedEntitySearchRefresher
    batch_enqueuer: ProjectIndexBatchEnqueuer
    workflow_starter: ProjectIndexWorkflowStarter | None = None
    fanout_failure_recorder: ProjectIndexFanoutFailureRecorder | None = None
    completion_relation_runtime: RelationResolutionRuntime | None = None
    embedding_vector_sync: EmbeddingBatchVectorSync | None = None
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

    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime: ...


def local_project_embedding_vector_sync(
    dependencies: LocalIndexProjectDependencies,
) -> EmbeddingBatchVectorSync | None:
    """Return the semantic vector sync backend when local config enables it."""
    app_config = dependencies.entity_service.app_config
    if app_config is None or not app_config.semantic_search_enabled:
        return None
    return dependencies.search_service


@dataclass(frozen=True, slots=True)
class LocalIndexFileBatchReader(IndexFileBatchReader[IndexInputFile]):
    """Load current local files for the shared index-file batch runner."""

    file_service: FileService

    async def read_current_files(
        self,
        file_paths: Sequence[str],
        *,
        max_concurrent: int,
    ) -> IndexFileBatchReadResult[IndexInputFile]:
        return await read_current_index_files(
            file_paths,
            reader=self,
            max_concurrent=max_concurrent,
        )

    async def read_current_file(
        self,
        file_path: str,
    ) -> IndexFileBatchReadOutcome[IndexInputFile]:
        try:
            file_bytes = await self.file_service.read_file_bytes(file_path)
            file_metadata = await self.file_service.get_file_metadata(file_path)
        except FileOperationError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return IndexFileBatchReadOutcome.terminal(
                    IndexFileJobResult(
                        status=IndexFileJobStatus.missing,
                        reason=f"file not found: {file_path}",
                    )
                )
            raise
        except FileNotFoundError:
            return IndexFileBatchReadOutcome.terminal(
                IndexFileJobResult(
                    status=IndexFileJobStatus.missing,
                    reason=f"file not found: {file_path}",
                )
            )

        return IndexFileBatchReadOutcome.loaded(
            IndexInputFile(
                path=file_path,
                size=file_metadata.size,
                checksum=await compute_checksum(file_bytes),
                content_type=self.file_service.content_type(file_path),
                last_modified=file_metadata.modified_at,
                created_at=file_metadata.created_at,
                content=file_bytes,
            )
        )


@dataclass(frozen=True, slots=True)
class LocalProjectIndexBatchEnqueuer(ProjectIndexBatchEnqueuer):
    """Run project-index child batches through the shared batch-index runner."""

    checker: IndexFileBatchChecker
    reader: IndexFileBatchReader[IndexInputFile]
    indexer: IndexFileBatchIndexer[IndexInputFile]
    content_classifier: IndexFileBatchContentClassifier
    read_max_concurrent: int = 8
    index_max_concurrent: int = 8

    async def enqueue_index_file_batch(
        self,
        request: RuntimeIndexFileBatchJobRequest,
    ) -> IndexFileBatchJobResult:
        return await run_index_file_batch(
            request,
            checker=self.checker,
            reader=self.reader,
            indexer=self.indexer,
            content_classifier=self.content_classifier,
            read_max_concurrent=self.read_max_concurrent,
            index_max_concurrent=self.index_max_concurrent,
        )


@dataclass(frozen=True, slots=True)
class LocalProjectIndexRuntimeFactory:
    """Build project-wide local indexing runtime dependencies for one project."""

    dependency_provider: LocalIndexProjectDependencyProvider = (
        DefaultLocalIndexProjectDependencyProvider()
    )
    batch_size: int = 100
    read_max_concurrent: int = 8
    index_max_concurrent: int = 8

    async def dependencies_for_project(self, project: Project) -> LocalIndexProjectDependencies:
        return await self.dependency_provider.dependencies_for_project(project)

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
        maintenance_store = RepositoryProjectIndexMaintenanceStore(
            session_maker=dependencies.session_maker,
            project_id=dependencies.project_id,
            move_content_updater=LocalProjectIndexMoveContentUpdater(
                entity_service=dependencies.entity_service,
                file_service=dependencies.file_service,
            ),
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
            moved_entity_search_refresher=RepositoryProjectIndexMovedEntitySearchRefresher(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
                entity_indexer=dependencies.search_service,
            ),
            batch_enqueuer=LocalProjectIndexBatchEnqueuer(
                checker=checker,
                reader=LocalIndexFileBatchReader(dependencies.file_service),
                indexer=dependencies.file_batch_indexer,
                content_classifier=dependencies.file_service,
                read_max_concurrent=self.read_max_concurrent,
                index_max_concurrent=self.index_max_concurrent,
            ),
            completion_relation_runtime=RepositoryRelationResolutionRuntime(
                session_maker=dependencies.session_maker,
                relation_repository=dependencies.relation_repository,
                entity_repository=dependencies.entity_repository,
                link_resolver=dependencies.link_resolver,
                entity_indexer=dependencies.search_service,
            ),
            embedding_vector_sync=local_project_embedding_vector_sync(dependencies),
            batch_size=self.batch_size,
        )

    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime:
        return self.runtime_from_dependencies(await self.dependencies_for_project(project))


async def run_local_project_index_for_project(
    project: Project,
    *,
    runtime_factory: LocalProjectIndexRuntimeProvider,
    force_full: bool = False,
    embeddings: bool = True,
) -> ProjectIndexCoordinatorResult:
    """Run local project indexing for one project through the core fanout runtime.

    ``embeddings`` threads the caller's choice into the request so a search-only
    reindex (``bm reindex --search``) does not collect vector targets or call the
    embedding provider; it defaults to True for all other callers.
    """
    return await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            project=ProjectRuntimeReference.from_project(project),
            force_full=force_full,
            embeddings=embeddings,
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
        moved_entity_search_refresher=runtime.moved_entity_search_refresher,
        workflow_starter=runtime.workflow_starter,
        batch_enqueuer=runtime.batch_enqueuer,
        fanout_failure_recorder=runtime.fanout_failure_recorder,
        batch_size=runtime.batch_size,
        embedding_vector_sync=runtime.embedding_vector_sync,
    )
    if runtime.completion_relation_runtime is not None:
        await resolve_project_index_completion_relations(
            ProjectIndexRelationResolutionContext(
                project_id=request.project.project_id,
                project_path=request.project.project_path,
            ),
            runtime.completion_relation_runtime,
        )
    return result
