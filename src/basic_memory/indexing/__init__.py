"""Reusable indexing primitives shared by local sync and future remote callers."""

from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.batching import build_index_batches
from basic_memory.indexing.change_planning import (
    ChangeReport,
    FileMoveCandidate,
    plan_file_changes,
    plan_moved_files,
)
from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexPlan,
    EmbeddingIndexPlanner,
    EmbeddingIndexTarget,
)
from basic_memory.indexing.file_index_planning import (
    FileIndexDecision,
    FileIndexDecisionStatus,
    FileIndexPlan,
    FileIndexTarget,
    build_file_index_plan,
    current_file_index_decision,
    plan_file_index_target_from_current,
    plan_file_index_target_from_observed,
    plan_legacy_file_index_targets,
)
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
from basic_memory.indexing.progress import (
    CheckpointModel,
    IndexingResult,
    IndexingResultState,
    VectorSyncProgress,
    VectorSyncProgressState,
)

__all__ = [
    "BatchIndexer",
    "ChangeReport",
    "CheckpointModel",
    "EmbeddingIndexPlan",
    "EmbeddingIndexPlanner",
    "EmbeddingIndexTarget",
    "FileIndexDecision",
    "FileIndexDecisionStatus",
    "FileIndexPlan",
    "FileIndexTarget",
    "FileMoveCandidate",
    "IndexedEntity",
    "IndexingResult",
    "IndexingResultState",
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
    "VectorSyncProgress",
    "VectorSyncProgressState",
    "build_file_index_plan",
    "build_index_batches",
    "current_file_index_decision",
    "plan_file_index_target_from_current",
    "plan_file_index_target_from_observed",
    "plan_legacy_file_index_targets",
    "plan_file_changes",
    "plan_moved_files",
    "plan_note_content_reconciliation",
]
