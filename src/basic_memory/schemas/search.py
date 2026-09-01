"""Search schemas for Basic Memory.

The search system supports three primary modes:
1. Exact permalink lookup
2. Pattern matching with *
3. Full-text search across content
"""

from typing import Optional, List, Union, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator

from basic_memory.schemas.base import Permalink, normalize_note_type


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


def normalize_file_path_prefix(value: Optional[str]) -> Optional[str]:
    """Reduce a directory scope to its bare project-relative spelling.

    "", "/", and "   " all name the project root — no subtree scope — and must
    collapse to None. "/" in particular is a non-empty string, so left as-is it
    would read as criteria to the service's has-criteria check while
    contributing no predicate: a query that filters nothing yet reports its
    total as if it had. "/specs/" and "specs" are the same scope, so they too
    normalize to the one value the SQL predicate and the criteria check share.
    """
    if value is None:
        return None
    normalized = value.strip().strip("/")
    return normalized or None


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
    - categories: Limit observation results to exact category matches (e.g. "requirement")
    - after_date: Only items after date
    - metadata_filters: Structured frontmatter filters (field -> value)
    - file_path_prefix: Limit to one directory subtree of the project
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
    categories: Optional[List[str]] = None  # Filter observations by exact category
    after_date: Optional[Union[datetime, str]] = None  # Time-based filter
    metadata_filters: Optional[dict[str, Any]] = None  # Structured frontmatter filters
    # Directory subtree scope, matched against the indexed file_path — not the
    # permalink, which stops mirroring its file path once a note pins one in
    # frontmatter or is moved with update_permalinks_on_move disabled.
    file_path_prefix: Optional[str] = None
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

    @field_validator("note_types")
    @classmethod
    def normalize_note_types(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        """Apply the same canonical identity used when note types are written."""
        if values is None:
            return None
        return [normalize_note_type(value) for value in values]

    @field_validator("file_path_prefix")
    @classmethod
    def normalize_scope(cls, value: Optional[str]) -> Optional[str]:
        """Collapse the root spellings onto "no scope" at the boundary.

        Parsing once here means every consumer — the criteria check, the
        executed-criteria description, and the SQL predicate — reads the same
        value instead of each rediscovering that "/" is not a subtree.
        """
        return normalize_file_path_prefix(value)

    def no_criteria(self) -> bool:
        text_is_empty = self.text is None or (isinstance(self.text, str) and not self.text.strip())
        metadata_is_empty = not self.metadata_filters
        tags_is_empty = not self.tags
        status_is_empty = self.status is None or (isinstance(self.status, str) and not self.status)
        note_types_is_empty = not self.note_types
        entity_types_is_empty = not self.entity_types
        categories_is_empty = not self.categories
        return (
            self.permalink is None
            and self.permalink_match is None
            and self.title is None
            and text_is_empty
            and self.after_date is None
            and note_types_is_empty
            and entity_types_is_empty
            and categories_is_empty
            and metadata_is_empty
            # Normalized above, so a bare "/" never counts as a scope here.
            and self.file_path_prefix is None
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
    # External UUID of the note this result belongs to (the parent entity for observation and
    # relation hits). Stable, API-friendly id the hosted MCP layer uses to build web-app
    # deep-links to the matching note (#1423).
    external_id: Optional[str] = None
    permalink: Optional[str]
    content: Optional[str] = None
    matched_chunk: Optional[str] = None
    file_path: str
    updated_at: Optional[datetime] = None

    metadata: Optional[dict[str, Any]] = None

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
    total: int = Field(
        default=0,
        description="Total matching results when total_is_exact is true; otherwise a sentinel or estimate",
    )
    total_is_exact: bool = Field(
        default=True,
        description="Whether total is an exact count that clients can use for pagination",
    )
    has_more: bool = False
