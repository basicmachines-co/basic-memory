"""Types and utilities for file sync."""

from dataclasses import dataclass, field
from typing import Set, Dict, Optional

from watchfiles import Change


@dataclass
class FileChange:
    """A change to a file detected by the watch service.

    Attributes:
        change_type: Type of change (added, modified, deleted)
        path: Path to the file
        checksum: File checksum (None for deleted files)
    """

    change_type: Change
    path: str
    checksum: Optional[str] = None


@dataclass
class SyncReport:
    """Report of file changes found compared to database state.

    Attributes:
        total: Total number of files in directory being synced
        new: Files that exist on disk but not in database
        modified: Files that exist in both but have different checksums
        deleted: Files that exist in database but not on disk
        moves: Files that have been moved from one location to another
        checksums: Current checksums for files on disk
    """

    total: int = 0
    # We keep paths as strings in sets/dicts for easier serialization
    new: Set[str] = field(default_factory=set)
    modified: Set[str] = field(default_factory=set)
    deleted: Set[str] = field(default_factory=set)
    moves: Dict[str, str] = field(default_factory=dict)  # old_path -> new_path
    checksums: Dict[str, str] = field(default_factory=dict)  # path -> checksum

    @property
    def total_changes(self) -> int:
        """Total number of changes."""
        return len(self.new) + len(self.modified) + len(self.deleted) + len(self.moves)
