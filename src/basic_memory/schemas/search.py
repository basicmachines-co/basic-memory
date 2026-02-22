"""Search schemas for Basic Memory.

The search system supports three primary modes:
1. Exact permalink lookup
2. Pattern matching with *
3. Full-text search across content
"""

from typing import Optional, List, Union, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, field_validator

from basic_memory.schemas.base import Permalink


class SearchItemType(str, Enum):
    """Types of searchable items."""

    ENTITY = "entity"
    OBSERVATION = "observation"
    RELATION = "relation"


class SearchRetrievalMode(str, Enum):
    """Retrieval strategy for text queries."""

    FTS = "fts"
    VECTOR = "vector"
    HYBRID = "hybrid"


class SearchQuery(BaseModel):
    """Search query parameters.

    Use ONE of these primary search modes:
    - permalink: Exact permalink match
    - permalink_match: Path pattern with *
    - text: Full-text search of title/content (supports boolean operators: AND, OR, NOT)
    - title: Title only search

    Optionally filter results by:
    - note_types: Limit to specific note types (frontmatter "type")
    - entity_types: Limit to search item types (entity/observation/relation)
    - after_date: Only items after date
    - metadata_filters: Structured frontmatter filters (field -> value)
    - tags: Convenience frontmatter tag filter
    - status: Convenience frontmatter status filter

    Boolean search examples:
    - "python AND flask" - Find items with both terms
    - "python OR django" - Find items with either term
    - "python NOT django" - Find items with python but not django
    - "(python OR flask) AND web" - Use parentheses for grouping
    """

    # Primary search modes (use ONE of these)
    permalink: Optional[str] = None  # Exact permalink match
    permalink_match: Optional[str] = None  # Glob permalink match
    text: Optional[str] = None  # Full-text search (now supports boolean operators)
    title: Optional[str] = None  # title only search

    # Optional filters
    note_types: Optional[List[str]] = None  # Filter by note type (frontmatter "type")
    entity_types: Optional[List[SearchItemType]] = None  # Filter by entity type
    after_date: Optional[Union[datetime, str]] = None  # Time-based filter
    metadata_filters: Optional[dict[str, Any]] = None  # Structured frontmatter filters
    tags: Optional[List[str]] = None  # Convenience tag filter
    status: Optional[str] = None  # Convenience status filter
    retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS
    min_similarity: Optional[float] = None  # Per-query override for semantic_min_similarity

    @field_validator("after_date")
    @classmethod
    def validate_date(cls, v: Optional[Union[datetime, str]]) -> Optional[str]:
        """Convert datetime to ISO format if needed."""
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    def no_criteria(self) -> bool:
        text_is_empty = self.text is None or (isinstance(self.text, str) and not self.text.strip())
        metadata_is_empty = not self.metadata_filters
        tags_is_empty = not self.tags
        status_is_empty = self.status is None or (isinstance(self.status, str) and not self.status)
        note_types_is_empty = not self.note_types
        entity_types_is_empty = not self.entity_types
        return (
            self.permalink is None
            and self.permalink_match is None
            and self.title is None
            and text_is_empty
            and self.after_date is None
            and note_types_is_empty
            and entity_types_is_empty
            and metadata_is_empty
            and tags_is_empty
            and status_is_empty
        )

    def has_boolean_operators(self) -> bool:
        """Check if the text query contains boolean operators (AND, OR, NOT)."""
        if not self.text:  # pragma: no cover
            return False

        # Check for common boolean operators with correct word boundaries
        # to avoid matching substrings like "GRAND" containing "AND"
        boolean_patterns = [" AND ", " OR ", " NOT ", "(", ")"]
        text = f" {self.text} "  # Add spaces to ensure we match word boundaries
        return any(pattern in text for pattern in boolean_patterns)


class SearchResult(BaseModel):
    """Search result with score and metadata."""

    title: str
    type: SearchItemType
    score: float
    entity: Optional[Permalink] = None
    permalink: Optional[str]
    content: Optional[str] = None
    file_path: str

    metadata: Optional[dict] = None

    # IDs for v2 API consistency
    entity_id: Optional[int] = None  # Entity ID (always present for entities)
    observation_id: Optional[int] = None  # Observation ID (for observation results)
    relation_id: Optional[int] = None  # Relation ID (for relation results)

    # Type-specific fields
    category: Optional[str] = None  # For observations
    from_entity: Optional[Permalink] = None  # For relations
    to_entity: Optional[Permalink] = None  # For relations
    relation_type: Optional[str] = None  # For relations


class SearchResponse(BaseModel):
    """Wrapper for search results."""

    results: List[SearchResult]
    current_page: int
    page_size: int
    has_more: bool = False
