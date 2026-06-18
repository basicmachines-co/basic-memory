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
    SyncedMarkdownFile,
)
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentBootstrap,
    NoteContentFileObserved,
    NoteContentFileSynced,
    NoteContentPromoted,
    NoteContentReconciliationPlan,
    NoteContentState,
    ObservedNoteContent,
    plan_note_content_reconciliation,
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
    "NoteContentBootstrap",
    "NoteContentFileObserved",
    "NoteContentFileSynced",
    "NoteContentPromoted",
    "NoteContentReconciliationPlan",
    "NoteContentState",
    "ObservedNoteContent",
    "SyncedMarkdownFile",
    "build_index_batches",
    "plan_note_content_reconciliation",
]
