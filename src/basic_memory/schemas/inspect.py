"""API schemas for note-level retrieval inspection."""

from datetime import datetime
from typing import Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, model_validator

type ChunkStatus = Literal["ready", "pending", "stale", "orphaned"]
type InspectFreshness = Literal[
    "fresh",
    "not_indexed",
    "index_behind_rows",
    "rows_behind_file",
    "unknown",
]
type InspectFileWriteStatus = Literal[
    "pending",
    "writing",
    "synced",
    "failed",
    "external_change_detected",
]


class InspectChunksRequest(BaseModel):
    """Request to inspect the retrieval projections for one note identifier."""

    identifier: str


class InspectChunkReadiness(BaseModel):
    """Mutually exclusive vector-chunk readiness counts.

    ``missing`` counts current chunks with no stored manifest row; ``total`` covers
    stored rows only.
    """

    total: int
    ready: int
    pending: int
    stale: int
    orphaned: int
    missing: int


class InspectChunk(BaseModel):
    """One vector chunk stored for a search row."""

    chunk_key: str
    ordinal: int
    text: str
    source_hash: str
    embedding_model: str
    vector_index: str
    status: ChunkStatus
    updated_at: datetime


class InspectSearchRow(BaseModel):
    """One search row and its current stored vector chunks."""

    type: str
    id: int
    title: str | None
    category: str | None
    relation_type: str | None
    content_preview: str | None
    chunks: list[InspectChunk]


class InspectDetachedSearchRow(BaseModel):
    """Stored chunks grouped by a source search row that no longer exists."""

    type: str
    id: int
    source_row_gone: Literal[True] = True
    chunks: list[InspectChunk]


class InspectIndexBehindRowsDetail(BaseModel):
    """Evidence that chunks trail, cannot serve, or do not cover current search rows."""

    model_config = ConfigDict(extra="forbid")

    entity_fingerprint_indexed: str | list[str] | None
    entity_fingerprint_current: str
    missing_chunk_count: int


class InspectRowsBehindFileDetail(BaseModel):
    """File and note-content evidence used for file-to-row freshness."""

    model_config = ConfigDict(extra="forbid")

    entity_checksum: str | None
    current_file_checksum: str | None
    db_checksum: str | None
    file_checksum: str | None
    file_write_status: InspectFileWriteStatus | None


type InspectFreshnessDetail = InspectIndexBehindRowsDetail | InspectRowsBehindFileDetail


class InspectChunksResponse(BaseModel):
    """Note identity, chunk readiness, and search-row decomposition."""

    entity_id: int
    external_id: str
    permalink: str | None
    file_path: str
    title: str
    entity_checksum: str | None
    configured_embedding_model: str
    configured_vector_index: str
    readiness: InspectChunkReadiness
    entity_fingerprint_indexed: str | list[str] | None
    entity_fingerprint_current: str
    stale: bool = Field(
        description=(
            "Whether stored vector chunks trail or cannot serve current search rows. This "
            "signal remains independent when upstream file freshness is unknown."
        )
    )
    freshness: InspectFreshness = Field(
        description=(
            "Upstream-first file-to-row-to-index classification. Unknown can coexist with "
            "stale=true when file bytes are unreadable but chunk fingerprints prove index lag."
        )
    )
    freshness_detail: InspectFreshnessDetail | None = Field(
        description=(
            "Evidence for the selected freshness state. When freshness is unknown, consult "
            "stale, readiness, and the fingerprint fields for independent row-to-index evidence."
        )
    )
    rows: list[InspectSearchRow]
    detached: list[InspectDetachedSearchRow]

    @model_validator(mode="after")
    def validate_freshness_detail(self) -> Self:
        """Keep the derived freshness value and its evidence in a valid combination."""
        match self.freshness:
            case "fresh" | "not_indexed":
                detail_is_valid = self.freshness_detail is None
            case "index_behind_rows":
                detail_is_valid = isinstance(
                    self.freshness_detail,
                    InspectIndexBehindRowsDetail,
                )
            case "rows_behind_file" | "unknown":
                detail_is_valid = isinstance(
                    self.freshness_detail,
                    InspectRowsBehindFileDetail,
                )
            case unexpected:  # pragma: no cover - InspectFreshness is exhaustive
                assert_never(unexpected)

        if not detail_is_valid:
            raise ValueError(f"Invalid detail for freshness={self.freshness}")
        return self
