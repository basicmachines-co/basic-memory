"""Portable indexing progress and checkpoint models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

type EntityId = int


class VectorSyncBatchSummary(Protocol):
    """Fields needed to fold one vector sync batch into durable progress."""

    entities_synced: int
    entities_failed: int
    failed_entity_ids: list[EntityId]
    embedding_jobs_total: int
    embed_seconds_total: float
    write_seconds_total: float


class CheckpointModel(BaseModel):
    """Shared base model for durable checkpoint JSON."""

    model_config = ConfigDict(extra="ignore")


class VectorSyncProgress(CheckpointModel):
    """Durable progress snapshot for chunked vector sync runs.

    This model is also the persisted checkpoint document: field names and the
    dumped JSON shape must stay stable so older checkpoints keep restoring.
    """

    entity_ids: list[int] = Field(default_factory=list)
    next_index: int = 0
    entities_synced: int = 0
    entities_failed: int = 0
    failed_entity_ids: list[int] = Field(default_factory=list)
    embedding_jobs_total: int = 0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0
    elapsed_seconds: float = 0.0

    @field_validator("entity_ids", "failed_entity_ids")
    @classmethod
    def dedupe_ids(cls, value: list[int]) -> list[int]:
        """Preserve order while removing duplicate ids."""
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def clamp_next_index(self) -> VectorSyncProgress:
        """Keep resume offsets inside the tracked entity list."""
        self.next_index = min(self.next_index, len(self.entity_ids))
        return self

    @computed_field
    @property
    def entities_total(self) -> int:
        """Total entity ids tracked by this progress snapshot."""
        return len(self.entity_ids)

    def without_entity_ids(self) -> VectorSyncProgress:
        """Return a progress snapshot without the static entity plan.

        model_copy skips validation on purpose: dropping the plan must keep
        the recorded counters (including next_index) exactly as they were.
        """
        return self.model_copy(
            update={
                "entity_ids": [],
                "failed_entity_ids": list(self.failed_entity_ids),
            }
        )

    def to_checkpoint_state(self) -> dict[str, object]:
        """Serialize vector progress into JSON-friendly workflow metadata."""
        rounded = self.model_copy(
            update={
                "embed_seconds_total": round(self.embed_seconds_total, 3),
                "write_seconds_total": round(self.write_seconds_total, 3),
                "elapsed_seconds": round(self.elapsed_seconds, 3),
            }
        )
        return rounded.model_dump(mode="json")

    @classmethod
    def from_checkpoint_state(cls, state: object) -> VectorSyncProgress:
        """Restore vector sync progress from workflow metadata."""
        if state is None:
            return cls()

        try:
            return cls.model_validate(state)
        except ValidationError:
            return cls()


def initialize_vector_sync_progress(
    *,
    entity_ids: Sequence[EntityId],
    resume_progress: VectorSyncProgress | None,
) -> VectorSyncProgress:
    """Create mutable vector progress for a chunked sync execution."""
    effective_entity_ids = (
        list(resume_progress.entity_ids)
        if resume_progress is not None and resume_progress.entity_ids
        else list(entity_ids)
    )
    total = len(effective_entity_ids)

    return VectorSyncProgress(
        entity_ids=effective_entity_ids,
        next_index=min(resume_progress.next_index, total) if resume_progress else 0,
        entities_synced=resume_progress.entities_synced if resume_progress else 0,
        entities_failed=resume_progress.entities_failed if resume_progress else 0,
        failed_entity_ids=list(resume_progress.failed_entity_ids) if resume_progress else [],
        embedding_jobs_total=resume_progress.embedding_jobs_total if resume_progress else 0,
        embed_seconds_total=resume_progress.embed_seconds_total if resume_progress else 0.0,
        write_seconds_total=resume_progress.write_seconds_total if resume_progress else 0.0,
        elapsed_seconds=resume_progress.elapsed_seconds if resume_progress else 0.0,
    )


def apply_vector_sync_batch_result(
    progress_state: VectorSyncProgress,
    batch_result: VectorSyncBatchSummary,
    *,
    next_index: int,
    elapsed_seconds: float,
) -> list[EntityId]:
    """Fold one batch result into mutable durable vector progress."""
    progress_state.next_index = next_index
    progress_state.entities_synced += batch_result.entities_synced
    progress_state.entities_failed += batch_result.entities_failed
    progress_state.embedding_jobs_total += batch_result.embedding_jobs_total
    progress_state.embed_seconds_total += batch_result.embed_seconds_total
    progress_state.write_seconds_total += batch_result.write_seconds_total
    progress_state.elapsed_seconds = elapsed_seconds

    failed_entity_ids_seen = set(progress_state.failed_entity_ids)
    new_failed_entity_ids: list[EntityId] = []
    for failed_entity_id in batch_result.failed_entity_ids:
        if failed_entity_id in failed_entity_ids_seen:
            continue
        failed_entity_ids_seen.add(failed_entity_id)
        progress_state.failed_entity_ids.append(failed_entity_id)
        new_failed_entity_ids.append(failed_entity_id)
    return new_failed_entity_ids


class IndexingResult(CheckpointModel):
    """Final result of an indexing operation.

    This model is also the persisted aggregate checkpoint for retry-safe
    workflows: field names and the dumped JSON shape must stay stable so
    older checkpoints keep restoring.
    """

    files_processed: int = 0
    files_unchanged: int = 0
    entities_created: int = 0
    entities_updated: int = 0
    entities_deleted: int = 0
    files_moved: int = 0
    forward_refs_resolved: int = 0
    relations_resolved: int = 0
    relations_unresolved: int = 0
    search_indexed: int = 0
    semantic_vector_entities_total: int = 0
    semantic_vectors_synced: int = 0
    semantic_vectors_failed: int = 0
    errors: list[tuple[str, str]] = Field(default_factory=list)
    total_duration_seconds: float = 0.0
    change_detection_seconds: float = 0.0
    s3_download_seconds: float = 0.0
    file_processing_seconds: float = 0.0
    relation_resolution_seconds: float = 0.0
    search_indexing_seconds: float = 0.0
    semantic_vector_sync_seconds: float = 0.0
    semantic_vector_embed_seconds: float = 0.0
    semantic_vector_write_seconds: float = 0.0
    peak_rss_mib: float = 0.0
    batch_count: int = 0

    @field_validator("errors", mode="before")
    @classmethod
    def normalize_errors(cls, value: object) -> object:
        """Accept legacy tuple/list error payloads and normalize them."""
        if not isinstance(value, list):
            return value

        normalized: list[tuple[str, str]] = []
        for item in value:
            if isinstance(item, list | tuple) and len(item) == 2:
                normalized.append((str(item[0]), str(item[1])))
                continue
            if isinstance(item, Mapping):
                item_payload = cast(Mapping[str, object], item)
                path = item_payload.get("path")
                error = item_payload.get("error")
                if path is not None and error is not None:
                    normalized.append((str(path), str(error)))
        return normalized

    @property
    def total_errors(self) -> int:
        """Total number of errors."""
        return len(self.errors)

    @property
    def success(self) -> bool:
        """Whether indexing completed without errors."""
        return self.total_errors == 0

    @property
    def files_per_second(self) -> float:
        """Processing rate in files per second."""
        if self.total_duration_seconds > 0:
            return self.files_processed / self.total_duration_seconds
        return 0.0

    @property
    def avg_batch_duration(self) -> float:
        """Average duration per batch in seconds."""
        if self.batch_count > 0:
            return self.file_processing_seconds / self.batch_count
        return 0.0

    def to_checkpoint_state(self) -> dict[str, object]:
        """Serialize the durable aggregate result for retry-safe workflows."""
        rounded = self.model_copy(
            update={
                "total_duration_seconds": round(self.total_duration_seconds, 3),
                "change_detection_seconds": round(self.change_detection_seconds, 3),
                "s3_download_seconds": round(self.s3_download_seconds, 3),
                "file_processing_seconds": round(self.file_processing_seconds, 3),
                "relation_resolution_seconds": round(self.relation_resolution_seconds, 3),
                "search_indexing_seconds": round(self.search_indexing_seconds, 3),
                "semantic_vector_sync_seconds": round(self.semantic_vector_sync_seconds, 3),
                "semantic_vector_embed_seconds": round(self.semantic_vector_embed_seconds, 3),
                "semantic_vector_write_seconds": round(self.semantic_vector_write_seconds, 3),
                "peak_rss_mib": round(self.peak_rss_mib, 3),
            }
        )
        return rounded.model_dump(mode="json")

    @classmethod
    def from_checkpoint_state(cls, state: object) -> IndexingResult:
        """Restore cumulative indexing state from workflow metadata."""
        if state is None:
            return cls()

        try:
            return cls.model_validate(state)
        except ValidationError:
            return cls()
