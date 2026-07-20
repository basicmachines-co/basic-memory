"""Search index data structures."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from basic_memory.schemas.search import SearchItemType
from basic_memory.utils import ensure_timezone_aware


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
    to_name: Optional[str] = None  # relations: literal target text, set even when unresolved
    relation_type: Optional[str] = None  # relations

    # Matched chunk text from vector search (the actual content that matched the query)
    matched_chunk_text: Optional[str] = None

    CONTENT_DISPLAY_LIMIT = 4000

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "SearchIndexRow":
        """Hydrate one persisted search row from either supported database backend."""
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = json.loads(metadata) if metadata else {}

        raw_score = row.get("score")
        return cls(
            project_id=row["project_id"],
            id=row["id"],
            title=row.get("title"),
            permalink=row.get("permalink"),
            file_path=row["file_path"],
            type=row["type"],
            score=float(raw_score) if raw_score is not None else None,
            metadata=metadata,
            from_id=row.get("from_id"),
            to_id=row.get("to_id"),
            to_name=row.get("to_name"),
            relation_type=row.get("relation_type"),
            entity_id=row.get("entity_id"),
            content_stems=row.get("content_stems"),
            content_snippet=row.get("content_snippet"),
            category=row.get("category"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def __post_init__(self) -> None:
        """Restore typed, timezone-aware datetimes from raw search query results."""
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at)
        self.created_at = ensure_timezone_aware(self.created_at)
        self.updated_at = ensure_timezone_aware(self.updated_at)

    @property
    def content(self):
        """Return truncated content for display. Full content in content_snippet."""
        if self.content_snippet and len(self.content_snippet) > self.CONTENT_DISPLAY_LIMIT:
            return self.content_snippet[: self.CONTENT_DISPLAY_LIMIT]
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
            "to_name": self.to_name,
            "relation_type": self.relation_type,
            "entity_id": self.entity_id,
            "category": self.category,
            "created_at": self.created_at if self.created_at else None,
            "updated_at": self.updated_at if self.updated_at else None,
            "project_id": self.project_id,
        }
