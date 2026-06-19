"""Tests for portable index-file batch orchestration."""

from collections.abc import Mapping, Sequence
from uuid import UUID

import pytest

from basic_memory.indexing.file_batch_runner import (
    IndexFileBatchReadResult,
    run_index_file_batch,
)
from basic_memory.indexing.file_index_planning import (
    FileIndexDecision,
    FileIndexDecisionStatus,
    FileIndexPlan,
    FileIndexTarget,
)
from basic_memory.indexing.models import (
    IndexedEntity,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexingBatchResult,
)
from basic_memory.runtime import ProjectRuntimeReference, RuntimeIndexFileBatchJobRequest


class LoadedFile:
    def __init__(self, path: str) -> None:
        self.path = path


class FakeChecker:
    def __init__(self) -> None:
        self.targets: tuple[FileIndexTarget, ...] | None = None

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
        self.targets = tuple(targets)
        return FileIndexPlan(
            paths_to_read=("notes/a.md", "notes/missing.md", "notes/broken.md"),
            decisions=(
                FileIndexDecision(
                    path="notes/current.md",
                    status=FileIndexDecisionStatus.current,
                    reason="file already indexed: notes/current.md",
                ),
            ),
        )


class FakeReader:
    def __init__(self) -> None:
        self.file_paths: tuple[str, ...] | None = None
        self.max_concurrent: int | None = None

    async def read_current_files(
        self,
        file_paths: Sequence[str],
        *,
        max_concurrent: int,
    ) -> IndexFileBatchReadResult[LoadedFile]:
        self.file_paths = tuple(file_paths)
        self.max_concurrent = max_concurrent
        return IndexFileBatchReadResult(
            files={
                "notes/a.md": LoadedFile("notes/a.md"),
                "notes/broken.md": LoadedFile("notes/broken.md"),
            },
            terminal_results={
                "notes/missing.md": IndexFileJobResult(
                    status=IndexFileJobStatus.missing,
                    reason="file not found: notes/missing.md",
                )
            },
        )


class FakeIndexer:
    def __init__(self) -> None:
        self.files: tuple[str, ...] | None = None
        self.max_concurrent: int | None = None
        self.parse_max_concurrent: int | None = None
        self.metadata_update_max_concurrent: int | None = None
        self.bound_logger: object | None = None

    async def index_files(
        self,
        files: Mapping[str, LoadedFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
        metadata_update_max_concurrent: int | None = None,
        bound_logger: object | None = None,
    ) -> IndexingBatchResult:
        self.files = tuple(files)
        self.max_concurrent = max_concurrent
        self.parse_max_concurrent = parse_max_concurrent
        self.metadata_update_max_concurrent = metadata_update_max_concurrent
        self.bound_logger = bound_logger
        return IndexingBatchResult(
            indexed=[
                IndexedEntity(
                    path="notes/a.md",
                    entity_id=42,
                    permalink="notes/a",
                    checksum="checksum-a",
                )
            ],
            errors=[("notes/broken.md", "parse failed")],
        )


class FakeClassifier:
    def is_markdown(self, path: str) -> bool:
        return path.endswith(".md")


@pytest.mark.asyncio
async def test_run_index_file_batch_reads_indexes_and_orders_results() -> None:
    checker = FakeChecker()
    reader = FakeReader()
    indexer = FakeIndexer()
    request = RuntimeIndexFileBatchJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project=ProjectRuntimeReference(
            project_id=101,
            project_external_id="project-main",
            project_path="main",
        ),
        workflow_id=UUID("22222222-2222-2222-2222-222222222222"),
        batch_index=0,
        batch_count=1,
        file_paths=(
            "notes/current.md",
            "notes/a.md",
            "notes/missing.md",
            "notes/broken.md",
        ),
        index_embeddings=True,
    )
    bound_logger = object()

    result = await run_index_file_batch(
        request,
        checker=checker,
        reader=reader,
        indexer=indexer,
        content_classifier=FakeClassifier(),
        read_max_concurrent=2,
        index_max_concurrent=3,
        bound_logger=bound_logger,
    )

    assert checker.targets == (
        FileIndexTarget(path="notes/current.md"),
        FileIndexTarget(path="notes/a.md"),
        FileIndexTarget(path="notes/missing.md"),
        FileIndexTarget(path="notes/broken.md"),
    )
    assert reader.file_paths == ("notes/a.md", "notes/missing.md", "notes/broken.md")
    assert reader.max_concurrent == 2
    assert indexer.files == ("notes/a.md", "notes/broken.md")
    assert indexer.max_concurrent == 3
    assert indexer.parse_max_concurrent == 3
    assert indexer.metadata_update_max_concurrent == 3
    assert indexer.bound_logger is bound_logger
    assert [file_result.status.value for file_result in result.file_results] == [
        "current",
        "processed",
        "missing",
        "failed",
    ]
    assert result.processed_files == 2
    assert result.missing_files == 1
    assert result.failed_files == 1
    assert [target.entity_id for target in result.vector_targets] == [42]
