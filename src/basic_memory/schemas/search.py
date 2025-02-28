"""Search schemas for Basic Memory.

The search system supports three primary modes:
1. Exact permalink lookup
2. Pattern matching with *
3. Full-text search across content
"""

from typing import Optional, List, Union
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, field_validator


class SearchItemType(str, Enum):
    """Types of searchable items."""

    ENTITY = "entity"
    OBSERVATION = "observation"
    RELATION = "relation"


class SearchQuery(BaseModel):
    """Search query parameters.

    Use ONE of these primary search modes:
    - permalink: Exact permalink match
    - permalink_match: Path pattern with *
    - text: Full-text search of title/content

    Optionally filter results by:
    - types: Limit to specific item types
    - entity_types: Limit to specific entity types
    - after_date: Only items after date
    """

    # Primary search modes (use ONE of these)
    permalink: Optional[str] = None  # Exact permalink match
    permalink_match: Optional[str] = None  # Exact permalink match
    text: Optional[str] = None  # Full-text search
    title: Optional[str] = None  # title only search

    # Optional filters
    types: Optional[List[SearchItemType]] = None  # Filter by item type
    entity_types: Optional[List[str]] = None  # Filter by entity type
    after_date: Optional[Union[datetime, str]] = None  # Time-based filter

    @field_validator("after_date")
    @classmethod
    def validate_date(cls, v: Optional[Union[datetime, str]]) -> Optional[str]:
        """Convert datetime to ISO format if needed."""
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    def no_criteria(self) -> bool:
        return (
            self.permalink is None
            and self.permalink_match is None
            and self.text is None
            and self.after_date is None
            and self.types is None
            and self.entity_types is None
        )


class SearchResult(BaseModel):
    """Search result with score and metadata."""

    id: int
    title: str
    type: SearchItemType
    score: float
    permalink: Optional[str]
    file_path: str

    metadata: Optional[dict] = None

    # Type-specific fields
    entity_id: Optional[int] = None  # For observations
    category: Optional[str] = None  # For observations
    from_id: Optional[int] = None  # For relations
    to_id: Optional[int] = None  # For relations
    relation_type: Optional[str] = None  # For relations


class RelatedResult(BaseModel):
    type: SearchItemType
    id: int
    title: str
    permalink: str
    depth: int
    root_id: int
    created_at: datetime
    from_id: Optional[int] = None
    to_id: Optional[int] = None
    relation_type: Optional[str] = None
    category: Optional[str] = None
    entity_id: Optional[int] = None


class SearchResponse(BaseModel):
    """Wrapper for search results."""

    results: List[SearchResult]
    current_page: int
    page_size: int


# Schema for future advanced search endpoint
class AdvancedSearchQuery(BaseModel):
    """Advanced full-text search with explicit FTS5 syntax."""

    query: str  # Raw FTS5 query (e.g., "foo AND bar")
    types: Optional[List[SearchItemType]] = None
    entity_types: Optional[List[str]] = None
    after_date: Optional[Union[datetime, str]] = None
