"""Search schemas for Basic Memory.

The search system supports three primary modes:
1. Exact permalink lookup
2. Pattern matching with *
3. Full-text search across content
"""

from typing import Optional, List, Union, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from basic_memory.schemas.base import Permalink, normalize_note_type
from basic_memory.temporal import TemporalQualifierError, reject_blank_temporal_value


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
    """Reduce a directory scope to the bare project-relative spelling the index stores.

    Exactly two things here are notation rather than path: a leading "./" and
    the surrounding separators. ``DirectoryService`` removes those two before it
    lists a directory, and ``find``'s two arms read one ``path`` argument — a
    scope that means "specs/" without ``meta`` and "./specs/" with it is the
    same argument asking two different questions, and the SQL prefix built from
    the second matches nothing, silently.

    Everything else survives byte for byte, whitespace included. A directory
    really can be named " specs ", and stripping would answer for "specs/"
    instead: a different subtree, reported under the same exact total as the
    right one. The preserved spelling gives the honest empty result.

    A spelling carrying no path at all — "", "/", "./", "   ", "  /  " — names
    the project root, i.e. no subtree scope, and must collapse to None. "/" in
    particular is a non-empty string, so left as-is it would read as criteria to
    the service's has-criteria check while contributing no predicate: a query
    that filters nothing yet reports its total as if it had.
    """
    if value is None:
        return None
    scope = value.removeprefix("./")
    if not scope.strip().strip("/"):
        return None
    return scope.strip("/")


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
    - valid_at / valid_overlaps / time_kind: Authored valid-time filters (SPEC-82)

    Valid time is what a note *says about the world*, written as a qualifier on an
    observation (``- [decision] @effective[2026-06-10,2026-07-27) ...``). It is a
    different axis from ``after_date``, which filters on when a row was last indexed
    and is deliberately left untouched by these fields.

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

    # Authored valid-time filters. Kept as strings at the boundary so HTTP clients and
    # MCP callers can pass one flat value; the service parses them into the portable
    # domain values and rejects anything malformed with a visible diagnostic.
    valid_at: Optional[str] = None  # Date or RFC 3339 instant the range must contain
    valid_overlaps: Optional[str] = None  # Range literal, e.g. "[2026-06-10,2026-07-27)"
    time_kind: Optional[str] = None  # effective | valid | occurred | due | mentioned

    @model_validator(mode="after")
    def validate_temporal_filter(self) -> "SearchQuery":
        """Refuse a query that asks two different valid-time questions at once."""
        if self.valid_at is not None and self.valid_overlaps is not None:
            raise ValueError(
                "Use either valid_at (containment) or valid_overlaps (overlap), not both."
            )
        return self

    @field_validator("valid_at", "valid_overlaps", "time_kind")
    @classmethod
    def check_temporal_value_not_blank(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        """Refuse a valid-time field that is present but empty.

        Enforced at the boundary as well as in the parser, because `no_criteria()` runs
        first: a query carrying only an empty `valid_at` would otherwise be turned away as
        having no criteria at all, which is true of the value and false of the request.
        The rule itself lives in `basic_memory.temporal` so the two cannot disagree.
        """
        try:
            reject_blank_temporal_value(info.field_name or "", value)
        except TemporalQualifierError as exc:
            raise ValueError(str(exc)) from exc
        return value

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

    def has_temporal_filter(self) -> bool:
        """Whether this query asks a valid-time question at all.

        A kind on its own is a legal filter: it asks for sources carrying any
        assertion of that kind. Callers use this to decide whether valid time was
        requested without parsing the values, which is why it never raises.
        """
        # Presence, not truthiness: a blank value is refused above, so anything that is
        # not None is a real question. Testing truthiness here is what let an empty
        # `valid_at` read as "no filter requested" and run the query unfiltered.
        return (
            self.valid_at is not None
            or self.valid_overlaps is not None
            or self.time_kind is not None
        )

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
            and not self.has_temporal_filter()
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


class TemporalRangeValue(BaseModel):
    """One authored interval, as a caller sees it.

    This is the single logical `valid_during` value the API and MCP boundary expose.
    How the projection stores it -- which table, which columns, which indexes -- is
    deliberately absent: `literal` is the canonical PostgreSQL range literal and the
    decomposed bounds are the same interval, spelled out so a caller can compare
    endpoints without parsing.

    Canonical means canonical: a *date* range always reads half-open, whatever brackets
    the author typed, because calendar dates are discrete. `source_text` on the
    enclosing `TemporalResultMetadata` is where the author's own spelling survives.
    """

    axis: str  # "date" (calendar dates) or "instant" (UTC timestamps)
    literal: str  # e.g. "[2026-06-10,2026-07-28)", "(,2026-07-27)", "empty"
    lower: Optional[str] = None  # None means unbounded on that side
    upper: Optional[str] = None
    lower_inclusive: bool = False
    upper_inclusive: bool = False
    is_empty: bool = False


class TemporalResultMetadata(BaseModel):
    """One authored valid-time assertion carried by a search result.

    Present so an agent can say *why* a source matched a valid-time query -- which
    kind of time it asserts, over what interval, and in the author's own words.
    """

    kind: str  # effective | valid | occurred | due | mentioned
    valid_during: TemporalRangeValue
    source_text: str  # the qualifier exactly as authored, e.g. "@effective[2026-06-10,)"


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
    content_length: Optional[int] = None
    content_truncated: Optional[bool] = None
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

    # Authored valid-time assertions carried by this row. Collection-shaped from day
    # one: the MVP parser reads one qualifier per observation, but multiple assertions
    # of multiple kinds must not be a schema break later.
    temporal: Optional[List[TemporalResultMetadata]] = None


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
    # Version-skew guard. SearchQuery ignores unknown fields, so a client that sends a
    # valid-time filter to a server predating SPEC-82 would receive unfiltered results
    # that look filtered -- silently including the undated sources the filter excludes.
    #
    # Three states, all meaningful: True (asked and executed), None (never asked, so
    # nothing to confirm), and -- only from a server that does not know this field --
    # missing, which parses as None while True was expected. Staying None rather than
    # False when no filter was asked keeps every ordinary search payload byte-identical
    # to what it was before valid time existed.
    temporal_applied: Optional[bool] = Field(
        default=None,
        description="True when the server executed a requested valid-time filter; "
        "absent when the request carried none",
    )
