"""Schemas for directory tree operations."""

from datetime import datetime
from typing import List, Optional, Literal

from pydantic import BaseModel


class DirectoryNode(BaseModel):
    """Directory node in file system."""

    name: str
    file_path: Optional[str] = None  # Original path without leading slash (matches DB)
    directory_path: str  # Path with leading slash for directory navigation
    type: Literal["directory", "file"]
    children: List["DirectoryNode"] = []  # Default to empty list
    title: Optional[str] = None
    permalink: Optional[str] = None
    entity_id: Optional[int] = None
    entity_type: Optional[str] = None
    content_type: Optional[str] = None
    updated_at: Optional[datetime] = None

    @property
    def has_children(self) -> bool:
        return bool(self.children)


# Support for recursive model
DirectoryNode.model_rebuild()
