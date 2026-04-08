"""Reusable indexing primitives shared by local sync and future remote callers."""

from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.batching import build_index_batches
from basic_memory.indexing.models import (
    IndexedEntity,
    IndexBatch,
    IndexFileMetadata,
    IndexFileWriter,
    IndexFrontmatterUpdate,
    IndexFrontmatterWriteResult,
    IndexingBatchResult,
    IndexInputFile,
    IndexProgress,
)

__all__ = [
    "BatchIndexer",
    "IndexedEntity",
    "IndexBatch",
    "IndexFileMetadata",
    "IndexFileWriter",
    "IndexFrontmatterUpdate",
    "IndexFrontmatterWriteResult",
    "IndexingBatchResult",
    "IndexInputFile",
    "IndexProgress",
    "build_index_batches",
]
