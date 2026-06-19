"""Typed models for the reusable indexing execution path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, TYPE_CHECKING

from basic_memory.indexing.embedding_index_planning import EmbeddingIndexTarget

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
