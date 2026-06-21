"""Tests for local project-wide event-index adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.index import (
    InlineProjectIndexBatchEnqueuer,
    LocalIndexProjectDependencies,
    LocalProjectIndexObservedFileSource,
    LocalProjectIndexFileRunner,
    LocalProjectIndexRuntimeFactory,
    LocalProjectIndexRuntime,
    NoopProjectIndexFanoutFailureRecorder,
    NoopProjectIndexWorkflowStarter,
    local_project_index_file_paths,
    project_index_file_requests_from_batch_request,
    run_local_project_index,
)
from basic_memory.indexing import (
    ChangeDetector,
    ChangeReport,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexFileObjectMetadata,
    IndexFileRuntimeRequest,
    FileIndexOperation,
    FileIndexResult,
    ProjectIndexDeleteRun,
    ProjectIndexMoveRun,
    ProjectIndexWorkflowRequest,
    RepositoryRelationResolutionRuntime,
    ResolvedRelationTarget,
    StoreProjectIndexMaintenanceRunner,
    UnresolvedRelation,
)
from basic_memory.models import Entity, Project
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
)
from basic_memory.services import FileService


TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WORKFLOW_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_local_project_index_file_paths_filter_and_sort(tmp_path: Path) -> None:
    """Local project scans use the same ignore and storage-event path rules."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "ignored").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "notes" / "b.md").write_text("# B\n", encoding="utf-8")
    (tmp_path / "notes" / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "notes" / "scratch.tmp").write_text("tmp\n", encoding="utf-8")
    (tmp_path / "ignored" / "skip.md").write_text("# Skip\n", encoding="utf-8")
    (tmp_path / ".hidden" / "secret.md").write_text("# Secret\n", encoding="utf-8")

    assert local_project_index_file_paths(tmp_path, ignore_patterns={"ignored"}) == (
        "notes/a.md",
        "notes/b.md",
    )


async def test_local_project_index_observed_file_source_returns_runtime_targets(
    tmp_path: Path,
) -> None:
    """Local project scans feed the same observed-file values as hosted storage."""
    (tmp_path / "notes").mkdir()
    note_path = tmp_path / "notes" / "a.md"
    note_content = "# A\n"
    note_path.write_text(note_content, encoding="utf-8")

    observed = await LocalProjectIndexObservedFileSource(
        FileService(tmp_path),
        ignore_patterns=set(),
    ).list_observed_index_files()

    assert observed == (
        RuntimeObservedIndexFile(
            path="notes/a.md",
            checksum=sha256(note_content.encode("utf-8")).hexdigest(),
            size=len(note_content.encode("utf-8")),
        ),
    )


@dataclass(slots=True)
class RecordingObservedFileSource:
    observed_files: tuple[RuntimeObservedIndexFile, ...]

    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        return self.observed_files


@dataclass(slots=True)
class RecordingChangeDetector:
    report: ChangeReport
    observed_paths: list[tuple[str, ...]] = field(default_factory=list)

    async def detect_all_changes(
        self,
        storage_files: Mapping[str, RuntimeObservedIndexFile],
    ) -> ChangeReport:
        self.observed_paths.append(tuple(storage_files))
        return self.report


@dataclass(slots=True)
class RecordingMaintenanceRunner:
    moved_files: list[dict[str, str]] = field(default_factory=list)
    deleted_paths: list[tuple[str, ...]] = field(default_factory=list)

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
        progress_callback: object | None = None,
    ) -> ProjectIndexMoveRun:
        self.moved_files.append(dict(moved_files))
        return ProjectIndexMoveRun(
            total_moves=len(moved_files),
            total_updated_files=len(moved_files),
            records=(),
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
        progress_callback: object | None = None,
    ) -> ProjectIndexDeleteRun:
        self.deleted_paths.append(tuple(deleted_paths))
        return ProjectIndexDeleteRun(
            total_deletes=len(deleted_paths),
            total_deleted_entities=len(deleted_paths),
            relation_cleanup_entity_ids=frozenset(),
            records=(),
        )


@dataclass(slots=True)
class RecordingBatchEnqueuer:
    requests: list[RuntimeIndexFileBatchJobRequest] = field(default_factory=list)

    async def enqueue_index_file_batch(self, request: RuntimeIndexFileBatchJobRequest) -> None:
        self.requests.append(request)


@dataclass(slots=True)
class RecordingCompletionRelationRuntime:
    events: list[str]
    resolve_calls: int = 0
    count_calls: int = 0

    async def count_unresolved_relations(self) -> int:
        self.events.append("relations:count")
        self.count_calls += 1
        return 2 if self.count_calls == 1 else 0

    async def resolve_relations(self) -> set[int]:
        self.events.append("relations:resolve")
        self.resolve_calls += 1
        return {10} if self.resolve_calls == 1 else set()


def project_ref() -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=12,
        project_external_id="project-12",
        project_name="Local",
        project_path="local-project",
    )


async def test_run_local_project_index_uses_core_project_fanout() -> None:
    """Local full-project indexing goes through the same coordinator as cloud."""
    observed = (
        RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=1),
        RuntimeObservedIndexFile(path="notes/b.md", checksum="b", size=2),
        RuntimeObservedIndexFile(path="notes/c.md", checksum="c", size=3),
    )
    observed_source = RecordingObservedFileSource(observed)
    change_detector = RecordingChangeDetector(
        ChangeReport(
            new_files=["notes/a.md", "notes/b.md", "notes/c.md"],
        )
    )
    maintenance_runner = RecordingMaintenanceRunner()
    batch_enqueuer = RecordingBatchEnqueuer()

    result = await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            tenant_id=TENANT_ID,
            workflow_id=WORKFLOW_ID,
            project=project_ref(),
            search=True,
            embeddings=False,
        ),
        runtime=LocalProjectIndexRuntime(
            observed_file_source=observed_source,
            change_detector=change_detector,
            maintenance_runner=maintenance_runner,
            batch_enqueuer=batch_enqueuer,
            workflow_starter=NoopProjectIndexWorkflowStarter(),
            fanout_failure_recorder=NoopProjectIndexFanoutFailureRecorder(),
            batch_size=2,
        ),
    )

    assert result.total_files == 3
    assert result.enqueued_batches == 2
    assert result.enqueued_files == 3
    assert change_detector.observed_paths == [("notes/a.md", "notes/b.md", "notes/c.md")]
    assert maintenance_runner.moved_files == [{}]
    assert maintenance_runner.deleted_paths == [()]
    assert [request.target_paths() for request in batch_enqueuer.requests] == [
        ("notes/a.md", "notes/b.md"),
        ("notes/c.md",),
    ]
    assert batch_enqueuer.requests[0].observed_files == observed[:2]
    assert batch_enqueuer.requests[0].index_embeddings is False


async def test_run_local_project_index_resolves_relations_after_inline_fanout() -> None:
    """Local project indexing runs completion relation resolution after child batches."""
    events: list[str] = []
    observed = (RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=1),)
    observed_source = RecordingObservedFileSource(observed)
    change_detector = RecordingChangeDetector(ChangeReport(new_files=["notes/a.md"]))
    maintenance_runner = RecordingMaintenanceRunner()

    class EventBatchEnqueuer(RecordingBatchEnqueuer):
        async def enqueue_index_file_batch(self, request: RuntimeIndexFileBatchJobRequest) -> None:
            events.append("batch")
            await super().enqueue_index_file_batch(request)

    relation_runtime = RecordingCompletionRelationRuntime(events)
    batch_enqueuer = EventBatchEnqueuer()

    await run_local_project_index(
        RuntimeProjectIndexJobRequest(
            tenant_id=TENANT_ID,
            workflow_id=WORKFLOW_ID,
            project=project_ref(),
            search=True,
            embeddings=False,
        ),
        runtime=LocalProjectIndexRuntime(
            observed_file_source=observed_source,
            change_detector=change_detector,
            maintenance_runner=maintenance_runner,
            batch_enqueuer=batch_enqueuer,
            workflow_starter=NoopProjectIndexWorkflowStarter(),
            fanout_failure_recorder=NoopProjectIndexFanoutFailureRecorder(),
            batch_size=10,
            completion_relation_runtime=relation_runtime,
        ),
    )

    assert events == [
        "batch",
        "relations:count",
        "relations:resolve",
        "relations:resolve",
        "relations:count",
    ]
    assert relation_runtime.resolve_calls == 2


async def test_noop_local_project_index_workflow_starter_returns_no_completion() -> None:
    """The first local runner does not persist UI workflow state."""
    completion = await NoopProjectIndexWorkflowStarter().start_project_index_workflow(
        ProjectIndexWorkflowRequest(
            tenant_id=TENANT_ID,
            workflow_id=WORKFLOW_ID,
            project=project_ref(),
            force_full=False,
            search=True,
            embeddings=True,
        ),
        total_files=1,
        batch_count=1,
        batch_size=100,
        coordinator_job_id=None,
    )

    assert completion is None


def test_project_index_file_requests_from_batch_request_preserve_observed_metadata() -> None:
    """Project batch requests become typed per-file index requests for inline runtimes."""
    batch_request = RuntimeIndexFileBatchJobRequest(
        tenant_id=TENANT_ID,
        project=project_ref(),
        workflow_id=WORKFLOW_ID,
        batch_index=0,
        batch_count=1,
        observed_files=(
            RuntimeObservedIndexFile(path="notes/a.md", checksum="etag-a", size=11),
            RuntimeObservedIndexFile(path="notes/b.md", checksum="etag-b", size=12),
        ),
        index_embeddings=False,
    )

    requests = project_index_file_requests_from_batch_request(batch_request)

    assert requests == (
        IndexFileRuntimeRequest(
            tenant_id=TENANT_ID,
            project_id=12,
            project_external_id="project-12",
            project_name="Local",
            project_path="local-project",
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.observed_object,
            object_observation=RuntimeStorageObjectObservation(etag="etag-a", size=11),
            index_embeddings=False,
            workflow_id=WORKFLOW_ID,
        ),
        IndexFileRuntimeRequest(
            tenant_id=TENANT_ID,
            project_id=12,
            project_external_id="project-12",
            project_name="Local",
            project_path="local-project",
            file_path="notes/b.md",
            mode=RuntimeStorageFileIndexMode.observed_object,
            object_observation=RuntimeStorageObjectObservation(etag="etag-b", size=12),
            index_embeddings=False,
            workflow_id=WORKFLOW_ID,
        ),
    )


def test_project_index_file_requests_from_batch_request_support_current_file_paths() -> None:
    """Inline local fanout can still run when only paths are present."""
    batch_request = RuntimeIndexFileBatchJobRequest(
        tenant_id=TENANT_ID,
        project=project_ref(),
        workflow_id=WORKFLOW_ID,
        batch_index=0,
        batch_count=1,
        file_paths=("notes/a.md",),
    )

    request = project_index_file_requests_from_batch_request(batch_request)[0]

    assert request.file_path == "notes/a.md"
    assert request.mode == RuntimeStorageFileIndexMode.current_file
    assert request.object_observation is None


@dataclass(slots=True)
class RecordingIndexFileRequestRunner:
    requests: list[IndexFileRuntimeRequest] = field(default_factory=list)

    async def run_index_file_request(
        self,
        request: IndexFileRuntimeRequest,
    ) -> IndexFileJobResult:
        self.requests.append(request)
        return IndexFileJobResult(status=IndexFileJobStatus.processed, reason="indexed")


async def test_inline_project_index_batch_enqueuer_runs_each_file_request() -> None:
    """Inline project fanout executes child batch requests in-process."""
    runner = RecordingIndexFileRequestRunner()
    enqueuer = InlineProjectIndexBatchEnqueuer(runner)

    await enqueuer.enqueue_index_file_batch(
        RuntimeIndexFileBatchJobRequest(
            tenant_id=TENANT_ID,
            project=project_ref(),
            workflow_id=WORKFLOW_ID,
            batch_index=0,
            batch_count=1,
            observed_files=(
                RuntimeObservedIndexFile(path="notes/a.md", checksum="etag-a", size=11),
                RuntimeObservedIndexFile(path="notes/b.md", checksum="etag-b", size=12),
            ),
            index_embeddings=False,
        )
    )

    assert [request.file_path for request in runner.requests] == ["notes/a.md", "notes/b.md"]
    assert [request.object_observation for request in runner.requests] == [
        RuntimeStorageObjectObservation(etag="etag-a", size=11),
        RuntimeStorageObjectObservation(etag="etag-b", size=12),
    ]


class NeverCalledChecker:
    async def detect(self, targets):
        raise AssertionError("current-file request should not call observed metadata checker")


class EmptyMaterializedNoteSource:
    async def load_current_materialized_note_entity(self, file_path: str):
        return None


@dataclass(slots=True)
class StaticMetadataSource:
    checksum: str

    async def load_current_file_metadata(self, file_path: str) -> IndexFileObjectMetadata | None:
        return IndexFileObjectMetadata(checksum=self.checksum)


@dataclass(slots=True)
class RecordingMarkdownFileIndexer:
    indexed_paths: list[str] = field(default_factory=list)

    async def index_markdown_file(self, file_path: str, *, source: str) -> FileIndexResult:
        self.indexed_paths.append(file_path)
        return FileIndexResult.from_fields(
            file_path=file_path,
            entity_id=99,
            external_id="note-99",
            title="Note 99",
            permalink="notes/note-99",
            checksum="indexed-checksum",
            operation=FileIndexOperation.updated,
        )


async def test_local_project_index_file_runner_runs_core_index_file() -> None:
    """Concrete local file runner uses the core per-file index runner."""
    file_indexer = RecordingMarkdownFileIndexer()
    runner = LocalProjectIndexFileRunner(
        checker=NeverCalledChecker(),
        metadata_source=StaticMetadataSource("current-checksum"),
        materialized_note_source=EmptyMaterializedNoteSource(),
        file_indexer=file_indexer,
    )

    result = await runner.run_index_file_request(
        IndexFileRuntimeRequest(
            tenant_id=TENANT_ID,
            project_id=12,
            project_external_id="project-12",
            project_name="Local",
            project_path="local-project",
            file_path="notes/a.md",
            mode=RuntimeStorageFileIndexMode.current_file,
            object_observation=None,
            index_embeddings=False,
            workflow_id=WORKFLOW_ID,
        )
    )

    assert result.status == IndexFileJobStatus.processed
    assert result.entity_id == 99
    assert file_indexer.indexed_paths == ["notes/a.md"]


class RuntimeFactoryEntityRepository:
    async def find_by_id(self, session: AsyncSession, entity_id: int) -> Entity | None:
        return None

    async def get_all_file_paths(self, session: AsyncSession) -> Sequence[str]:
        return ()

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: Path | str,
        *,
        load_relations: bool = True,
    ) -> Entity | None:
        return None

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[Path | str],
    ) -> Sequence[Row[Any]]:
        return ()

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[Any],
    ) -> Sequence[Entity]:
        return ()

    async def find_by_checksums(
        self,
        session: AsyncSession,
        checksums: Sequence[str],
    ) -> Sequence[Entity]:
        return ()

    async def update(
        self,
        session: AsyncSession,
        entity_id: Any,
        entity_data: dict[str, Any] | Entity,
    ) -> Entity | None:
        return None

    async def delete_by_fields(
        self,
        session: AsyncSession,
        **filters: object,
    ) -> bool:
        return False


class RuntimeFactorySearchIndex:
    async def handle_delete(self, entity: Entity) -> None:
        return None

    async def index_entity(self, entity: Entity) -> None:
        return None


class RuntimeFactoryRelationRepository:
    async def find_unresolved_relations(
        self, session: AsyncSession
    ) -> Sequence[UnresolvedRelation]:
        return ()

    async def find_unresolved_relations_for_entity(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> Sequence[UnresolvedRelation]:
        return ()

    async def update(
        self,
        session: AsyncSession,
        entity_id: int,
        entity_data: dict[str, object],
    ) -> object | None:
        return None

    async def delete(self, session: AsyncSession, entity_id: int) -> bool:
        return False


class RuntimeFactoryLinkResolver:
    async def resolve_link(
        self,
        link_text: str,
        *,
        strict: bool,
        session: AsyncSession,
    ) -> ResolvedRelationTarget | None:
        return None


async def test_local_project_index_runtime_factory_composes_inline_runtime(
    tmp_path: Path,
) -> None:
    """Local project indexing can be wired from explicit index dependencies."""
    dependencies = LocalIndexProjectDependencies(
        file_service=FileService(tmp_path),
        file_indexer=RecordingMarkdownFileIndexer(),
        session_maker=async_sessionmaker(),
        project_id=12,
        entity_repository=RuntimeFactoryEntityRepository(),
        relation_repository=RuntimeFactoryRelationRepository(),
        link_resolver=RuntimeFactoryLinkResolver(),
        search_service=RuntimeFactorySearchIndex(),
    )
    seen_projects: list[Project] = []

    async def dependency_provider(project: Project) -> LocalIndexProjectDependencies:
        seen_projects.append(project)
        return dependencies

    factory = LocalProjectIndexRuntimeFactory(
        dependency_provider=dependency_provider,
        batch_size=3,
    )
    project = Project(
        id=12,
        external_id="project-12",
        name="Local",
        permalink="local",
        path="local-project",
    )

    runtime = await factory.runtime_for_project(project)

    assert seen_projects == [project]
    assert isinstance(runtime.observed_file_source, LocalProjectIndexObservedFileSource)
    assert isinstance(runtime.change_detector, ChangeDetector)
    assert isinstance(runtime.maintenance_runner, StoreProjectIndexMaintenanceRunner)
    assert isinstance(runtime.completion_relation_runtime, RepositoryRelationResolutionRuntime)
    assert isinstance(runtime.batch_enqueuer, InlineProjectIndexBatchEnqueuer)
    assert isinstance(runtime.batch_enqueuer.file_runner, LocalProjectIndexFileRunner)
    assert runtime.batch_size == 3
