"""Portable indexing progress and checkpoint models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)


class CheckpointModel(BaseModel):
    """Shared base model for durable checkpoint JSON."""

    model_config = ConfigDict(extra="ignore")


class VectorSyncProgressState(CheckpointModel):
    """JSON payload for durable vector sync progress."""

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
    def clamp_next_index(self) -> "VectorSyncProgressState":
        """Keep resume offsets inside the tracked entity list."""
        self.next_index = min(self.next_index, len(self.entity_ids))
        return self

    @computed_field
    @property
    def entities_total(self) -> int:
        """Total entity ids tracked by this progress snapshot."""
        return len(self.entity_ids)


class IndexingResultState(CheckpointModel):
    """JSON payload for durable aggregate indexing state."""

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


@dataclass(slots=True)
class VectorSyncProgress:
    """Durable progress snapshot for chunked vector sync runs."""

    entity_ids: list[int] = field(default_factory=list)
    next_index: int = 0
    entities_synced: int = 0
    entities_failed: int = 0
    failed_entity_ids: list[int] = field(default_factory=list)
    embedding_jobs_total: int = 0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0
    elapsed_seconds: float = 0.0

    @property
    def entities_total(self) -> int:
        """Total number of entity IDs tracked by this vector sync run."""
        return len(self.entity_ids)

    def without_entity_ids(self) -> "VectorSyncProgress":
        """Return a progress snapshot without the static entity plan."""
        return VectorSyncProgress(
            next_index=self.next_index,
            entities_synced=self.entities_synced,
            entities_failed=self.entities_failed,
            failed_entity_ids=list(self.failed_entity_ids),
            embedding_jobs_total=self.embedding_jobs_total,
            embed_seconds_total=self.embed_seconds_total,
            write_seconds_total=self.write_seconds_total,
            elapsed_seconds=self.elapsed_seconds,
        )

    def to_checkpoint_state(self) -> dict[str, object]:
        """Serialize vector progress into JSON-friendly workflow metadata."""
        return VectorSyncProgressState(
            entity_ids=list(self.entity_ids),
            next_index=self.next_index,
            entities_synced=self.entities_synced,
            entities_failed=self.entities_failed,
            failed_entity_ids=list(self.failed_entity_ids),
            embedding_jobs_total=self.embedding_jobs_total,
            embed_seconds_total=round(self.embed_seconds_total, 3),
            write_seconds_total=round(self.write_seconds_total, 3),
            elapsed_seconds=round(self.elapsed_seconds, 3),
        ).model_dump(mode="json")

    @classmethod
    def from_checkpoint_state(cls, state: object) -> "VectorSyncProgress":
        """Restore vector sync progress from workflow metadata."""
        if state is None:
            return cls()

        try:
            payload = VectorSyncProgressState.model_validate(state)
        except ValidationError:
            return cls()

        return cls(
            entity_ids=payload.entity_ids,
            next_index=payload.next_index,
            entities_synced=payload.entities_synced,
            entities_failed=payload.entities_failed,
            failed_entity_ids=payload.failed_entity_ids,
            embedding_jobs_total=payload.embedding_jobs_total,
            embed_seconds_total=payload.embed_seconds_total,
            write_seconds_total=payload.write_seconds_total,
            elapsed_seconds=payload.elapsed_seconds,
        )


@dataclass(slots=True)
class IndexingResult:
    """Final result of an indexing operation."""

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
    errors: list[tuple[str, str]] = field(default_factory=list)
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
        return IndexingResultState(
            files_processed=self.files_processed,
            files_unchanged=self.files_unchanged,
            entities_created=self.entities_created,
            entities_updated=self.entities_updated,
            entities_deleted=self.entities_deleted,
            files_moved=self.files_moved,
            forward_refs_resolved=self.forward_refs_resolved,
            relations_resolved=self.relations_resolved,
            relations_unresolved=self.relations_unresolved,
            search_indexed=self.search_indexed,
            semantic_vector_entities_total=self.semantic_vector_entities_total,
            semantic_vectors_synced=self.semantic_vectors_synced,
            semantic_vectors_failed=self.semantic_vectors_failed,
            errors=list(self.errors),
            total_duration_seconds=round(self.total_duration_seconds, 3),
            change_detection_seconds=round(self.change_detection_seconds, 3),
            s3_download_seconds=round(self.s3_download_seconds, 3),
            file_processing_seconds=round(self.file_processing_seconds, 3),
            relation_resolution_seconds=round(self.relation_resolution_seconds, 3),
            search_indexing_seconds=round(self.search_indexing_seconds, 3),
            semantic_vector_sync_seconds=round(self.semantic_vector_sync_seconds, 3),
            semantic_vector_embed_seconds=round(self.semantic_vector_embed_seconds, 3),
            semantic_vector_write_seconds=round(self.semantic_vector_write_seconds, 3),
            peak_rss_mib=round(self.peak_rss_mib, 3),
            batch_count=self.batch_count,
        ).model_dump(mode="json")

    @classmethod
    def from_checkpoint_state(cls, state: object) -> "IndexingResult":
        """Restore cumulative indexing state from workflow metadata."""
        if state is None:
            return cls()

        try:
            payload = IndexingResultState.model_validate(state)
        except ValidationError:
            return cls()

        return cls(
            files_processed=payload.files_processed,
            files_unchanged=payload.files_unchanged,
            entities_created=payload.entities_created,
            entities_updated=payload.entities_updated,
            entities_deleted=payload.entities_deleted,
            files_moved=payload.files_moved,
            forward_refs_resolved=payload.forward_refs_resolved,
            relations_resolved=payload.relations_resolved,
            relations_unresolved=payload.relations_unresolved,
            search_indexed=payload.search_indexed,
            semantic_vector_entities_total=payload.semantic_vector_entities_total,
            semantic_vectors_synced=payload.semantic_vectors_synced,
            semantic_vectors_failed=payload.semantic_vectors_failed,
            errors=payload.errors,
            total_duration_seconds=payload.total_duration_seconds,
            change_detection_seconds=payload.change_detection_seconds,
            s3_download_seconds=payload.s3_download_seconds,
            file_processing_seconds=payload.file_processing_seconds,
            relation_resolution_seconds=payload.relation_resolution_seconds,
            search_indexing_seconds=payload.search_indexing_seconds,
            semantic_vector_sync_seconds=payload.semantic_vector_sync_seconds,
            semantic_vector_embed_seconds=payload.semantic_vector_embed_seconds,
            semantic_vector_write_seconds=payload.semantic_vector_write_seconds,
            peak_rss_mib=payload.peak_rss_mib,
            batch_count=payload.batch_count,
        )
