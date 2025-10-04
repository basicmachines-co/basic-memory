"""Pydantic schemas for sync report responses."""

from typing import TYPE_CHECKING, Dict, Set

from pydantic import BaseModel, Field

# avoid cirular imports
if TYPE_CHECKING:
    from basic_memory.sync.sync_service import SyncReport


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
            total=report.total,
        )

    model_config = {"from_attributes": True}
