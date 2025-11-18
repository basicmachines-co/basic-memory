"""Search index data structures."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from pathlib import Path

from basic_memory.schemas.search import SearchItemType


@dataclass
class SearchIndexRow:
    """Search result with score and metadata."""

    project_id: int
    id: int
    type: str
    file_path: str

    # date values
    created_at: datetime
    updated_at: datetime

    permalink: Optional[str] = None
    metadata: Optional[dict] = None

    # assigned in result
    score: Optional[float] = None

    # Type-specific fields
    title: Optional[str] = None  # entity
    content_stems: Optional[str] = None  # entity, observation
    content_snippet: Optional[str] = None  # entity, observation
    entity_id: Optional[int] = None  # observations
    category: Optional[str] = None  # observations
    from_id: Optional[int] = None  # relations
    to_id: Optional[int] = None  # relations
    relation_type: Optional[str] = None  # relations

    @property
    def content(self):
        return self.content_snippet

    @property
    def directory(self) -> str:
        """Extract directory part from file_path.

        For a file at "projects/notes/ideas.md", returns "/projects/notes"
        For a file at root level "README.md", returns "/"
        """
        if not self.type == SearchItemType.ENTITY.value and not self.file_path:
            return ""

        # Normalize path separators to handle both Windows (\) and Unix (/) paths
        normalized_path = Path(self.file_path).as_posix()

        # Split the path by slashes
        parts = normalized_path.split("/")

        # If there's only one part (e.g., "README.md"), it's at the root
        if len(parts) <= 1:
            return "/"

        # Join all parts except the last one (filename)
        directory_path = "/".join(parts[:-1])
        return f"/{directory_path}"

    def to_insert(self, serialize_json: bool = True):
        """Convert to dict for database insertion.

        Args:
            serialize_json: If True, converts metadata dict to JSON string (for SQLite).
                           If False, keeps metadata as dict (for Postgres JSONB).
        """
        return {
            "id": self.id,
            "title": self.title,
            "content_stems": self.content_stems,
            "content_snippet": self.content_snippet,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "type": self.type,
            "metadata": json.dumps(self.metadata)
            if serialize_json and self.metadata
            else self.metadata,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation_type": self.relation_type,
            "entity_id": self.entity_id,
            "category": self.category,
            "created_at": self.created_at if self.created_at else None,
            "updated_at": self.updated_at if self.updated_at else None,
            "project_id": self.project_id,
        }
