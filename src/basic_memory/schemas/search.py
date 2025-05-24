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

from basic_memory.schemas.base import Permalink


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
    - text: Full-text search of title/content (supports boolean operators: AND, OR, NOT)

    Optionally filter results by:
    - types: Limit to specific item types
    - entity_types: Limit to specific entity types
    - after_date: Only items after date

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
    types: Optional[List[str]] = None  # Filter by type
    entity_types: Optional[List[SearchItemType]] = None  # Filter by entity type
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
            and self.title is None
            and self.text is None
            and self.after_date is None
            and self.types is None
            and self.entity_types is None
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
