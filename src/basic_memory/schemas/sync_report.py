"""Pydantic schemas for sync report responses."""

from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Set

from pydantic import BaseModel, Field

# avoid cirular imports
if TYPE_CHECKING:
    from basic_memory.sync.sync_service import SyncReport


class SkippedFileResponse(BaseModel):
    """Information about a file that was skipped due to repeated failures."""

    path: str = Field(description="File path relative to project root")
    reason: str = Field(description="Error message from last failure")
    failure_count: int = Field(description="Number of consecutive failures")
    first_failed: datetime = Field(description="Timestamp of first failure")

    model_config = {"from_attributes": True}


class SyncReportResponse(BaseModel):
    """Report of file changes found compared to database state.

    Used for API responses when scanning or syncing files.
    """

    new: Set[str] = Field(default_factory=set, description="Files on disk but not in database")
    modified: Set[str] = Field(default_factory=set, description="Files with different checksums")
    deleted: Set[str] = Field(default_factory=set, description="Files in database but not on disk")
    moves: Dict[str, str] = Field(
        default_factory=dict, description="Files moved (old_path -> new_path)"
    )
    checksums: Dict[str, str] = Field(
        default_factory=dict, description="Current file checksums (path -> checksum)"
    )
    skipped_files: List[SkippedFileResponse] = Field(
        default_factory=list, description="Files skipped due to repeated failures"
    )
    total: int = Field(description="Total number of changes")

    @classmethod
    def from_sync_report(cls, report: "SyncReport") -> "SyncReportResponse":
        """Convert SyncReport dataclass to Pydantic model.

        Args:
            report: SyncReport dataclass from sync service

        Returns:
            SyncReportResponse with same data
        """
        return cls(
            new=report.new,
            modified=report.modified,
            deleted=report.deleted,
            moves=report.moves,
            checksums=report.checksums,
            skipped_files=[
                SkippedFileResponse(
                    path=skipped.path,
                    reason=skipped.reason,
                    failure_count=skipped.failure_count,
                    first_failed=skipped.first_failed,
                )
                for skipped in report.skipped_files
            ],
            total=report.total,
        )

    model_config = {"from_attributes": True}
