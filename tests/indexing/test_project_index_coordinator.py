"""Tests for the storage-neutral project-index coordinator fan-out."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from basic_memory.indexing import (
    ChangeReport,
    EmbeddingIndexTarget,
    IndexFileBatchJobResult,
    IndexFileJobResult,
    IndexFileJobStatus,
    ProjectIndexBatchJobPlan,
    ProjectIndexDeleteRun,
    ProjectIndexMoveRun,
    ProjectIndexRequest,
    build_project_index_batch_job_plan,
    run_project_index_coordinator,
)
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
)


@dataclass(frozen=True, slots=True)
class ProjectIndexSource:
    project_id: int
    project_external_id: str
    project_name: str | None
    project_permalink: str | None
    project_path: str
    force_full: bool
    search: bool
    embeddings: bool


@dataclass(slots=True)
class StaticObservedFileSource:
    observed_files: tuple[RuntimeObservedIndexFile, ...]

    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        return self.observed_files


@dataclass(slots=True)
class StaticChangeDetector:
    change_report: ChangeReport

    async def detect_all_changes(
        self,
        storage_files: Mapping[str, RuntimeObservedIndexFile],
    ) -> ChangeReport:
        return self.change_report


@dataclass(slots=True)
class EmptyProjectIndexMaintenanceRunner:
    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
        metadata_reporter: object | None = None,
    ) -> ProjectIndexMoveRun:
        return ProjectIndexMoveRun(
            total_moves=len(moved_files),
            total_updated_files=0,
            records=(),
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
        metadata_reporter: object | None = None,
    ) -> ProjectIndexDeleteRun:
        return ProjectIndexDeleteRun(
            total_deletes=len(deleted_paths),
            total_deleted_entities=0,
            relation_cleanup_entity_ids=frozenset(),
            records=(),
        )


@dataclass(slots=True)
class RecordingMovedEntitySearchRefresher:
    refreshed_entity_ids: list[list[int]] = field(default_factory=list)

    async def refresh_moved_entities(self, entity_ids: Sequence[int]) -> None:
        self.refreshed_entity_ids.append(list(entity_ids))


@dataclass(slots=True)
class StaticProjectIndexBatchEnqueuer:
    result: IndexFileBatchJobResult
    requests: list[RuntimeIndexFileBatchJobRequest] = field(default_factory=list)

    async def enqueue_index_file_batch(
        self,
        request: RuntimeIndexFileBatchJobRequest,
    ) -> IndexFileBatchJobResult:
        self.requests.append(request)
        return self.result


@dataclass(frozen=True, slots=True)
class RecordingEmbeddingBatchSummary:
    entities_synced: int
    entities_skipped: int = 0
    entities_failed: int = 0
    entities_deferred: int = 0


@dataclass(slots=True)
class RecordingEmbeddingVectorSync:
    entity_batches: list[list[int]] = field(default_factory=list)

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
    ) -> RecordingEmbeddingBatchSummary:
        self.entity_batches.append(entity_ids)
        return RecordingEmbeddingBatchSummary(entities_synced=len(entity_ids))


def test_project_index_request_serializes_existing_payload_metadata() -> None:
    request = ProjectIndexRequest.from_source(
        ProjectIndexSource(
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            force_full=True,
            search=True,
            embeddings=False,
        )
    )

    assert request.workflow_payload_metadata() == {
        "project_id": 42,
        "project_external_id": "external-project",
        "project_name": "Project Name",
        "project_permalink": "project-name",
        "project_path": "project",
        "force_full": True,
        "search": True,
        "embeddings": False,
    }


def test_project_index_batch_job_plan_builds_runtime_batch_requests() -> None:
    request = ProjectIndexRequest.from_source(
        ProjectIndexSource(
            project_id=42,
            project_external_id="external-project",
            project_name="Project Name",
            project_permalink="project-name",
            project_path="project",
            force_full=False,
            search=True,
            embeddings=False,
        )
    )
    observed_files = (
        RuntimeObservedIndexFile(path="notes/a.md", checksum="a", size=10),
        RuntimeObservedIndexFile(path="notes/b.txt", checksum="b", size=20),
        RuntimeObservedIndexFile(path="notes/c.md", checksum="c", size=30),
    )

    plan = build_project_index_batch_job_plan(
        request=request,
        observed_files=observed_files,
        batch_size=2,
    )

    assert plan == ProjectIndexBatchJobPlan(
        total_files=3,
        batch_count=2,
        batch_requests=(
            RuntimeIndexFileBatchJobRequest(
                project=request.project,
                batch_index=0,
                batch_count=2,
                file_paths=("notes/a.md", "notes/b.txt"),
                observed_files=observed_files[:2],
                index_embeddings=False,
            ),
            RuntimeIndexFileBatchJobRequest(
                project=request.project,
                batch_index=1,
                batch_count=2,
                file_paths=("notes/c.md",),
                observed_files=observed_files[2:],
                index_embeddings=False,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_project_index_coordinator_syncs_inline_vector_targets() -> None:
    batch_result = IndexFileBatchJobResult(
        total_files=2,
        processed_files=2,
        missing_files=0,
        failed_files=0,
        file_results=(
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/a.md",
                entity_id=42,
                entity_checksum="checksum-a",
            ),
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed: notes/b.md",
                entity_id=43,
                entity_checksum="checksum-b",
            ),
        ),
        vector_targets=(
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-a"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-a"),
            EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-b"),
        ),
    )
    batch_enqueuer = StaticProjectIndexBatchEnqueuer(batch_result)
    vector_sync = RecordingEmbeddingVectorSync()

    result = await run_project_index_coordinator(
        RuntimeProjectIndexJobRequest(
            project=ProjectRuntimeReference(
                project_id=7,
                project_external_id="external-project",
                project_path="/tmp/project",
                project_name="Project Name",
                project_permalink="project-name",
            ),
            force_full=False,
            search=True,
            embeddings=True,
        ),
        coordinator_job_id=None,
        observed_file_source=StaticObservedFileSource(
            observed_files=(
                RuntimeObservedIndexFile(path="notes/a.md", checksum="checksum-a", size=10),
                RuntimeObservedIndexFile(path="notes/b.md", checksum="checksum-b", size=20),
            )
        ),
        change_detector=StaticChangeDetector(ChangeReport(new_files=["notes/a.md", "notes/b.md"])),
        maintenance_runner=EmptyProjectIndexMaintenanceRunner(),
        moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
        workflow_starter=None,
        batch_enqueuer=batch_enqueuer,
        fanout_failure_recorder=None,
        batch_size=10,
        embedding_vector_sync=vector_sync,
    )

    assert vector_sync.entity_batches == [[42, 43]]
    assert result.batch_results == (batch_result,)
