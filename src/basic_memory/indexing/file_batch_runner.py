"""Portable orchestration for index-file batch jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.indexing.file_index_planning import (
    FileIndexPath,
    FileIndexPlan,
    FileIndexTarget,
    file_index_targets_from_runtime_batch_request,
)
from basic_memory.indexing.models import (
    IndexedEntity,
    IndexFileBatchJobResult,
    IndexFileJobResult,
    IndexingBatchResult,
    build_index_file_batch_job_result,
    index_file_job_result_from_decision,
)
from basic_memory.runtime import RuntimeIndexFileBatchJobRequest


class IndexFileBatchChecker(Protocol):
    """Capability that decides which batch targets need current file content."""

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan: ...


@dataclass(frozen=True, slots=True)
class IndexFileBatchReadResult[LoadedFileT]:
    """Current file reads plus terminal results discovered while reading."""

    files: Mapping[FileIndexPath, LoadedFileT]
    terminal_results: Mapping[FileIndexPath, IndexFileJobResult]


@dataclass(frozen=True, slots=True)
class IndexFileBatchReadOutcome[LoadedFileT]:
    """One current-file read outcome before batch result assembly."""

    file: LoadedFileT | None = None
    terminal_result: IndexFileJobResult | None = None

    def __post_init__(self) -> None:
        has_file = self.file is not None
        has_terminal_result = self.terminal_result is not None
        if has_file == has_terminal_result:
            raise ValueError("index-file batch read outcome requires exactly one result")

    @classmethod
    def loaded(cls, file: LoadedFileT) -> Self:
        """Return a successfully loaded file outcome."""
        return cls(file=file)

    @classmethod
    def terminal(cls, result: IndexFileJobResult) -> Self:
        """Return a terminal per-file result discovered during read."""
        return cls(terminal_result=result)

    def require_file(self) -> LoadedFileT:
        """Return the loaded file, raising when this is a terminal outcome."""
        if self.file is None:
            raise RuntimeError("index-file batch read outcome does not contain a file")
        return self.file


class IndexFileBatchCurrentFileReader[LoadedFileT](Protocol):
    """Capability that reads one current file and reports terminal misses."""

    async def read_current_file(
        self,
        file_path: FileIndexPath,
    ) -> IndexFileBatchReadOutcome[LoadedFileT]: ...


class IndexFileBatchReader[LoadedFileT](Protocol):
    """Capability that loads current file content for planned index reads."""

    async def read_current_files(
        self,
        file_paths: Sequence[FileIndexPath],
        *,
        max_concurrent: int,
    ) -> IndexFileBatchReadResult[LoadedFileT]: ...


class IndexFileBatchIndexer[LoadedFileT](Protocol):
    """Capability that indexes loaded batch files."""

    async def index_files(
        self,
        files: Mapping[FileIndexPath, LoadedFileT],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
        metadata_update_max_concurrent: int | None = None,
        bound_logger: object | None = None,
    ) -> IndexingBatchResult: ...


class IndexFileBatchContentClassifier(Protocol):
    """Capability that classifies paths for follow-up embedding work."""

    def is_markdown(self, path: FileIndexPath) -> bool: ...


async def read_current_index_files[LoadedFileT](
    file_paths: Sequence[FileIndexPath],
    *,
    reader: IndexFileBatchCurrentFileReader[LoadedFileT],
    max_concurrent: int,
) -> IndexFileBatchReadResult[LoadedFileT]:
    """Read current files concurrently while preserving per-file terminal results."""
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be at least 1")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def read_one(
        file_path: FileIndexPath,
    ) -> tuple[FileIndexPath, IndexFileBatchReadOutcome[LoadedFileT]]:
        async with semaphore:
            return file_path, await reader.read_current_file(file_path)

    files: dict[FileIndexPath, LoadedFileT] = {}
    terminal_results: dict[FileIndexPath, IndexFileJobResult] = {}
    for file_path, outcome in await asyncio.gather(
        *(read_one(file_path) for file_path in file_paths)
    ):
        if outcome.terminal_result is not None:
            terminal_results[file_path] = outcome.terminal_result
        else:
            files[file_path] = outcome.require_file()

    return IndexFileBatchReadResult(files=files, terminal_results=terminal_results)


async def run_index_file_batch[LoadedFileT](
    request: RuntimeIndexFileBatchJobRequest,
    *,
    checker: IndexFileBatchChecker,
    reader: IndexFileBatchReader[LoadedFileT],
    indexer: IndexFileBatchIndexer[LoadedFileT],
    content_classifier: IndexFileBatchContentClassifier,
    read_max_concurrent: int,
    index_max_concurrent: int,
    bound_logger: object | None = None,
) -> IndexFileBatchJobResult:
    """Run one index-file batch through storage-neutral capabilities."""
    target_paths = request.target_paths()
    if not target_paths:
        return IndexFileBatchJobResult(
            total_files=0,
            processed_files=0,
            missing_files=0,
            failed_files=0,
            file_results=(),
            vector_targets=(),
        )

    file_index_plan = await checker.detect(file_index_targets_from_runtime_batch_request(request))
    terminal_results: dict[FileIndexPath, IndexFileJobResult] = {
        decision.path: index_file_job_result_from_decision(decision)
        for decision in file_index_plan.decisions
    }

    read_result = await reader.read_current_files(
        file_index_plan.paths_to_read,
        max_concurrent=read_max_concurrent,
    )
    terminal_results.update(read_result.terminal_results)

    indexed_files: tuple[IndexedEntity, ...]
    errors: Mapping[FileIndexPath, str]
    if read_result.files:
        index_result = await indexer.index_files(
            read_result.files,
            max_concurrent=index_max_concurrent,
            parse_max_concurrent=index_max_concurrent,
            metadata_update_max_concurrent=index_max_concurrent,
            bound_logger=bound_logger,
        )
        indexed_files = tuple(index_result.indexed)
        errors = dict(index_result.errors)
    else:
        indexed_files = ()
        errors = {}

    embedding_eligible_paths = (
        tuple(
            indexed_file.path
            for indexed_file in indexed_files
            if content_classifier.is_markdown(indexed_file.path)
        )
        if request.index_embeddings
        else ()
    )
    return build_index_file_batch_job_result(
        target_paths=target_paths,
        terminal_results=terminal_results,
        indexed_files=indexed_files,
        errors=errors,
        index_embeddings=request.index_embeddings,
        embedding_eligible_paths=embedding_eligible_paths,
    )
