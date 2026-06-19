"""Typed models for the reusable indexing execution path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, TYPE_CHECKING

from basic_memory.indexing.embedding_index_planning import EmbeddingIndexTarget
from basic_memory.runtime import (
    RuntimeFileChecksum,
    RuntimeNoteChangeSource,
    RuntimeNoteObjectMetadataMap,
    RuntimeNoteObjectProvenance,
    RuntimeStorageObjectChecksumSource,
    db_version_from_object_metadata,
    storage_object_checksum_for_index_match,
)

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.models import Entity


@dataclass(slots=True)
class IndexFileMetadata:
    """Storage-agnostic metadata for a file queued for indexing."""

    path: str
    size: int
    checksum: str | None = None
    content_type: str | None = None
    last_modified: datetime | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class IndexInputFile(IndexFileMetadata):
    """Fully loaded file payload consumed by the batch executor."""

    content: bytes | None = None


@dataclass(slots=True)
class IndexBatch:
    """A deterministic batch of files bounded by count and total bytes."""

    paths: list[str]
    total_bytes: int


@dataclass(slots=True)
class IndexProgress:
    """Batch indexing progress emitted to callers such as the CLI."""

    files_total: int
    files_processed: int
    batches_total: int
    batches_completed: int
    current_batch_bytes: int = 0
    files_per_minute: float = 0.0
    eta_seconds: float | None = None


@dataclass(slots=True)
class IndexFrontmatterUpdate:
    """A typed frontmatter write request for a single file."""

    path: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class IndexFrontmatterWriteResult:
    """Typed result for a frontmatter write performed during indexing."""

    checksum: str
    content: str


@dataclass(slots=True)
class IndexedEntity:
    """Stable output describing one file that finished indexing successfully."""

    path: str
    entity_id: int
    permalink: str | None
    checksum: str
    content_type: str | None = None
    markdown_content: str | None = None


class FileIndexOperation(StrEnum):
    """Database operation used for one indexed markdown file."""

    created = "created"
    updated = "updated"


def file_index_operation_from_note_object_metadata(
    metadata: RuntimeNoteObjectMetadataMap | None,
) -> FileIndexOperation | None:
    """Infer the file-index operation from accepted note object metadata."""
    db_version = db_version_from_object_metadata(metadata)
    if db_version is None:
        return None
    return FileIndexOperation.created if db_version == 1 else FileIndexOperation.updated


@dataclass(frozen=True, slots=True)
class FileIndexResult:
    """Result for one successfully indexed markdown file."""

    file_path: str
    entity_id: int
    external_id: str
    title: str
    permalink: str
    checksum: str
    operation: FileIndexOperation

    @classmethod
    def from_fields(
        cls,
        *,
        file_path: str,
        entity_id: int,
        external_id: object,
        title: object,
        permalink: object,
        checksum: str,
        operation: FileIndexOperation,
    ) -> FileIndexResult:
        """Validate entity fields loaded for a completed file-index result."""
        return cls(
            file_path=file_path,
            entity_id=entity_id,
            external_id=_required_file_index_result_text(
                external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=_required_file_index_result_text(
                title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=_required_file_index_result_text(
                permalink,
                field_name="permalink",
                file_path=file_path,
            ),
            checksum=checksum,
            operation=operation,
        )


def _required_file_index_result_text(
    value: object,
    *,
    field_name: str,
    file_path: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Indexed entity for {file_path} is missing {field_name}")
    return value.strip()


class IndexFileJobStatus(StrEnum):
    """Normal outcomes for an index-file job."""

    processed = "processed"
    current = "current"
    missing = "missing"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class IndexFileJobResult:
    """Summary of what happened while processing one file-index job."""

    status: IndexFileJobStatus
    reason: str
    entity_id: int | None = None
    note_external_id: str | None = None
    title: str | None = None
    permalink: str | None = None
    entity_checksum: str | None = None
    operation: FileIndexOperation | None = None
    actor_user_profile_id: str | None = None
    actor_kind: str | None = None
    actor_name: str | None = None
    live_update_source: str | None = None


@dataclass(frozen=True, slots=True)
class CurrentMaterializedNoteEntity:
    """Indexed entity state needed to build a current materialized note result."""

    entity_id: int
    external_id: str
    title: str
    permalink: str
    checksum: RuntimeFileChecksum | None

    @classmethod
    def from_fields(
        cls,
        *,
        entity_id: int,
        external_id: object,
        title: object,
        permalink: object,
        checksum: object,
        file_path: str,
    ) -> CurrentMaterializedNoteEntity:
        """Validate entity fields loaded for a current materialized note."""
        return cls(
            entity_id=entity_id,
            external_id=_required_current_materialized_note_text(
                external_id,
                field_name="external_id",
                file_path=file_path,
            ),
            title=_required_current_materialized_note_text(
                title,
                field_name="title",
                file_path=file_path,
            ),
            permalink=_required_current_materialized_note_text(
                permalink,
                field_name="permalink",
                file_path=file_path,
            ),
            checksum=str(checksum) if checksum is not None else None,
        )


@dataclass(frozen=True, slots=True)
class CurrentMaterializedNotePlan:
    """Planned current-file result plus checksum diagnostics for adapter logging."""

    job_result: IndexFileJobResult
    requires_entity: bool = False
    object_checksum_source: RuntimeStorageObjectChecksumSource | None = None
    object_checksum: RuntimeFileChecksum | None = None
    entity_checksum: RuntimeFileChecksum | None = None
    source: RuntimeNoteChangeSource | None = None
    checksum_matches_entity: bool | None = None


@dataclass(frozen=True, slots=True)
class IndexedFileLiveUpdatePlan:
    """Trusted live-update metadata for one freshly indexed file."""

    object_checksum_source: RuntimeStorageObjectChecksumSource
    object_checksum: RuntimeFileChecksum
    indexed_checksum: RuntimeFileChecksum
    checksum_matches_indexed_file: bool
    metadata_actor_user_profile_id: str | None = None
    metadata_actor_kind: str | None = None
    metadata_actor_name: str | None = None
    metadata_source: RuntimeNoteChangeSource | None = None
    actor_user_profile_id: str | None = None
    actor_kind: str | None = None
    actor_name: str | None = None
    live_update_source: RuntimeNoteChangeSource | None = None
    operation: FileIndexOperation | None = None


def _required_current_materialized_note_text(
    value: object,
    *,
    field_name: str,
    file_path: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Current entity for {file_path} is missing {field_name}")
    return value.strip()


def plan_current_materialized_note_result(
    *,
    reason: str,
    file_path: str,
    object_checksum: RuntimeFileChecksum,
    object_metadata: RuntimeNoteObjectMetadataMap | None,
    entity: CurrentMaterializedNoteEntity | None,
) -> CurrentMaterializedNotePlan:
    """Plan a current file-index result for a DB-first materialized note."""
    current_result = IndexFileJobResult(status=IndexFileJobStatus.current, reason=reason)

    provenance = RuntimeNoteObjectProvenance.from_object_metadata(object_metadata)
    if provenance.source is None:
        return CurrentMaterializedNotePlan(job_result=current_result)

    live_update_operation = file_index_operation_from_note_object_metadata(object_metadata)
    if live_update_operation is None:
        return CurrentMaterializedNotePlan(
            job_result=current_result,
            source=provenance.source,
        )

    if entity is None:
        return CurrentMaterializedNotePlan(
            job_result=current_result,
            requires_entity=True,
            source=provenance.source,
        )

    selected_checksum = storage_object_checksum_for_index_match(
        object_checksum=object_checksum,
        object_metadata=object_metadata,
    )
    checksum_matches_entity = selected_checksum.checksum == entity.checksum
    plan = CurrentMaterializedNotePlan(
        job_result=current_result,
        object_checksum_source=selected_checksum.source,
        object_checksum=selected_checksum.checksum,
        entity_checksum=entity.checksum,
        source=provenance.source,
        checksum_matches_entity=checksum_matches_entity,
    )
    if not checksum_matches_entity:
        return plan

    return CurrentMaterializedNotePlan(
        job_result=IndexFileJobResult(
            status=IndexFileJobStatus.current,
            reason=reason,
            entity_id=entity.entity_id,
            note_external_id=entity.external_id,
            title=entity.title,
            permalink=entity.permalink,
            entity_checksum=entity.checksum,
            operation=live_update_operation,
            actor_user_profile_id=provenance.actor_user_profile_id,
            actor_kind=provenance.actor_kind,
            actor_name=provenance.actor_name,
            live_update_source=provenance.source,
        ),
        object_checksum_source=plan.object_checksum_source,
        object_checksum=plan.object_checksum,
        entity_checksum=plan.entity_checksum,
        source=plan.source,
        checksum_matches_entity=True,
    )


def plan_indexed_file_live_update_metadata(
    *,
    indexed_file: FileIndexResult,
    object_checksum: RuntimeFileChecksum,
    object_metadata: RuntimeNoteObjectMetadataMap | None,
) -> IndexedFileLiveUpdatePlan:
    """Plan trusted actor/source metadata for a freshly indexed file."""
    selected_checksum = storage_object_checksum_for_index_match(
        object_checksum=object_checksum,
        object_metadata=object_metadata,
    )
    provenance = RuntimeNoteObjectProvenance.from_object_metadata(object_metadata)
    checksum_matches_indexed_file = selected_checksum.checksum == indexed_file.checksum
    plan = IndexedFileLiveUpdatePlan(
        object_checksum_source=selected_checksum.source,
        object_checksum=selected_checksum.checksum,
        indexed_checksum=indexed_file.checksum,
        checksum_matches_indexed_file=checksum_matches_indexed_file,
        metadata_actor_user_profile_id=provenance.actor_user_profile_id,
        metadata_actor_kind=provenance.actor_kind,
        metadata_actor_name=provenance.actor_name,
        metadata_source=provenance.source,
    )
    if not checksum_matches_indexed_file:
        return plan

    return IndexedFileLiveUpdatePlan(
        object_checksum_source=plan.object_checksum_source,
        object_checksum=plan.object_checksum,
        indexed_checksum=plan.indexed_checksum,
        checksum_matches_indexed_file=True,
        metadata_actor_user_profile_id=plan.metadata_actor_user_profile_id,
        metadata_actor_kind=plan.metadata_actor_kind,
        metadata_actor_name=plan.metadata_actor_name,
        metadata_source=plan.metadata_source,
        actor_user_profile_id=provenance.actor_user_profile_id,
        actor_kind=provenance.actor_kind,
        actor_name=provenance.actor_name,
        live_update_source=provenance.source,
        operation=file_index_operation_from_note_object_metadata(object_metadata),
    )


@dataclass(frozen=True, slots=True)
class IndexFileBatchJobResult:
    """Summary of what happened while processing one file-index batch job."""

    total_files: int
    processed_files: int
    missing_files: int
    failed_files: int
    file_results: tuple[IndexFileJobResult, ...]
    vector_targets: tuple[EmbeddingIndexTarget, ...]


@dataclass(slots=True)
class SyncedMarkdownFile:
    """Canonical result for syncing one markdown file end-to-end."""

    entity: Entity
    checksum: str
    markdown_content: str
    file_path: str
    content_type: str
    updated_at: datetime
    size: int


@dataclass(slots=True)
class IndexingBatchResult:
    """Outcome for one batch execution."""

    indexed: list[IndexedEntity] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    relations_resolved: int = 0
    relations_unresolved: int = 0
    search_indexed: int = 0


class IndexFileWriter(Protocol):
    """Narrow protocol for frontmatter writes during indexing."""

    async def write_frontmatter(
        self, update: IndexFrontmatterUpdate
    ) -> IndexFrontmatterWriteResult: ...
