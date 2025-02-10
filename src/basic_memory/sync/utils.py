"""Types and utilities for file sync."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Set, Dict, Optional, Tuple, Union

from watchfiles import Change

from basic_memory.services.file_service import FileService


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

    @classmethod
    async def from_path(cls, path: str, change_type: Change, file_service: FileService) -> "FileChange":
        """Create FileChange from a path, computing checksum if file exists.
        
        Args:
            path: Path to the file
            change_type: Type of change detected
            file_service: Service to read file and compute checksum
            
        Returns:
            FileChange with computed checksum for non-deleted files
        """
        file_path = file_service.path(path)
        content, checksum = await file_service.read_file(file_path) if change_type != Change.deleted else (None, None)
        return cls(path=file_path, change_type=change_type, checksum=checksum)


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

    @property
    def synced_files(self) -> int:
        """Total number of files synced."""
        return len(self.new) + len(self.modified) + len(self.moves)