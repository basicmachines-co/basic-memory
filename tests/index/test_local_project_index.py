"""Tests for local project-wide event-index adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from basic_memory.index import (
    LocalProjectIndexObservedFileSource,
    LocalProjectIndexRuntime,
    NoopProjectIndexFanoutFailureRecorder,
    NoopProjectIndexWorkflowStarter,
    local_project_index_file_paths,
    run_local_project_index,
)
from basic_memory.indexing import OrphanEntityCleanupResult, ProjectIndexWorkflowRequest
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
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
class RecordingOrphanCleaner:
    current_paths: list[set[str]] = field(default_factory=list)

    async def cleanup_orphans(self, current_paths: set[str]) -> OrphanEntityCleanupResult:
        self.current_paths.append(current_paths)
        return OrphanEntityCleanupResult(
            orphan_paths=(),
            deleted_paths=(),
            skipped_missing_paths=(),
            skipped_changed_paths=(),
        )


@dataclass(slots=True)
class RecordingBatchEnqueuer:
    requests: list[RuntimeIndexFileBatchJobRequest] = field(default_factory=list)

    async def enqueue_index_file_batch(self, request: RuntimeIndexFileBatchJobRequest) -> None:
        self.requests.append(request)


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
    orphan_cleaner = RecordingOrphanCleaner()
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
            orphan_cleaner=orphan_cleaner,
            batch_enqueuer=batch_enqueuer,
            workflow_starter=NoopProjectIndexWorkflowStarter(),
            fanout_failure_recorder=NoopProjectIndexFanoutFailureRecorder(),
            batch_size=2,
        ),
    )

    assert result.total_files == 3
    assert result.enqueued_batches == 2
    assert result.enqueued_files == 3
    assert orphan_cleaner.current_paths == [{"notes/a.md", "notes/b.md", "notes/c.md"}]
    assert [request.target_paths() for request in batch_enqueuer.requests] == [
        ("notes/a.md", "notes/b.md"),
        ("notes/c.md",),
    ]
    assert batch_enqueuer.requests[0].observed_files == observed[:2]
    assert batch_enqueuer.requests[0].index_embeddings is False


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
