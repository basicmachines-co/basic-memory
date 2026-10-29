"""Schemas for memory context."""

from datetime import datetime
from typing import List, Optional, Annotated, Sequence, Literal, Union

from annotated_types import MinLen, MaxLen
from pydantic import BaseModel, Field, BeforeValidator, TypeAdapter, field_serializer

from basic_memory.schemas.search import SearchItemType


DEFAULT_CONTEXT_PAGE_SIZE = 10
MAX_CONTEXT_PAGE_SIZE = 50
DEFAULT_CONTEXT_RELATED_RESULTS = 10
MAX_CONTEXT_RELATED_RESULTS = 100
# The relation CTE walks every simple path up to twice this many steps, so on a
# dense graph its cost grows exponentially with depth. Five matches the prompt
# API's existing cap and keeps the documented 1-3 range comfortably inside it.
MAX_CONTEXT_DEPTH = 5
# Recent activity accepts larger pages than graph context because its default
# request is note-only summaries, not full context bodies.
MAX_RECENT_ACTIVITY_PAGE_SIZE = 100


def validate_memory_url_path(path: str) -> bool:
    """Validate that a memory URL path is well-formed.

    Args:
        path: The path part of a memory URL (without memory:// prefix)

    Returns:
        True if the path is valid, False otherwise

    Examples:
        >>> validate_memory_url_path("specs/search")
        True
        >>> validate_memory_url_path("memory//test")  # Double slash
        False
        >>> validate_memory_url_path("invalid://test")  # Contains protocol
        False
    """
    # Empty paths are not valid
    if not path or not path.strip():
        return False

    # Check for invalid protocol schemes within the path first (more specific)
    if "://" in path:
        return False

    # Check for double slashes (except at the beginning for absolute paths)
    if "//" in path:
        return False

    # Check for invalid characters (excluding * which is used for pattern matching)
    invalid_chars = {"<", ">", '"', "|", "?"}
    if any(char in path for char in invalid_chars):
        return False

    return True


def normalize_memory_url(url: str | None) -> str:
    """Normalize a MemoryUrl string with validation.

    Args:
        url: A path like "specs/search" or "memory://specs/search"

    Returns:
        Normalized URL starting with memory://

    Raises:
        ValueError: If the URL path is malformed

    Examples:
        >>> normalize_memory_url("specs/search")
        'memory://specs/search'
        >>> normalize_memory_url("memory://specs/search")
        'memory://specs/search'
        >>> normalize_memory_url("memory//test")
        Traceback (most recent call last):
        ...
        ValueError: Invalid memory URL path: 'memory//test' contains double slashes
    """
    if not url:
        raise ValueError("Memory URL cannot be empty")

    # Strip whitespace for consistency
    url = url.strip()

    if not url:
        raise ValueError("Memory URL cannot be empty or whitespace")

    clean_path = url.removeprefix("memory://")

    # Validate the extracted path
    if not validate_memory_url_path(clean_path):
        # Provide specific error messages for common issues
        if "://" in clean_path:
            raise ValueError(f"Invalid memory URL path: '{clean_path}' contains protocol scheme")
        elif "//" in clean_path:
            raise ValueError(f"Invalid memory URL path: '{clean_path}' contains double slashes")
        else:
            raise ValueError(f"Invalid memory URL path: '{clean_path}' contains invalid characters")

    return f"memory://{clean_path}"


MemoryUrl = Annotated[
    str,
    BeforeValidator(str.strip),  # Clean whitespace
    BeforeValidator(normalize_memory_url),  # Validate and normalize the URL
    MinLen(1),
    MaxLen(2028),
]

memory_url = TypeAdapter(MemoryUrl)


def memory_url_path(url: str) -> str:
    """
    Returns the uri for a url value by removing the prefix "memory://" from a given MemoryUrl.

    This function processes a given MemoryUrl by removing the "memory://"
    prefix and returns the resulting string. If the provided url does not
    begin with "memory://", the function will simply return the input url
    unchanged.

    :param url: A MemoryUrl object representing the URL with a "memory://" prefix.
    :type url: MemoryUrl
    :return: A string representing the URL with the "memory://" prefix removed.
    :rtype: str
    """
    return url.removeprefix("memory://")


class EntitySummary(BaseModel):
    """Simplified entity representation."""

    type: Literal["entity"] = "entity"
    external_id: str  # UUID for v2 API routing
    # COMPAT(v0.18): old clients expect these fields in JSON
    entity_id: Optional[int] = None
    permalink: Optional[str]
    title: str
    content: Optional[str] = None
    file_path: str
    created_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]
    # Optional: older servers do not send it, and context rows reached by relation
    # traversal do not load it.
    updated_at: Annotated[
        Optional[datetime], Field(json_schema_extra={"type": "string", "format": "date-time"})
    ] = None

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        return dt.isoformat()

    @field_serializer("updated_at")
    def serialize_updated_at(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


class RelationSummary(BaseModel):
    """Simplified relation representation."""

    type: Literal["relation"] = "relation"
    # COMPAT(v0.18): old clients expect these fields in JSON
    relation_id: Optional[int] = None
    entity_id: Optional[int] = None
    title: str
    file_path: str
    permalink: str
    relation_type: str
    from_entity: Optional[str] = None
    from_entity_id: Optional[int] = None
    from_entity_external_id: Optional[str] = None
    to_entity: Optional[str] = None
    # Literal target text from the markdown; present even when the relation is
    # an unresolved forward reference (to_entity is None until the target exists)
    to_name: Optional[str] = None
    to_entity_id: Optional[int] = None
    to_entity_external_id: Optional[str] = None
    created_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        return dt.isoformat()


class ObservationSummary(BaseModel):
    """Simplified observation representation."""

    type: Literal["observation"] = "observation"
    # COMPAT(v0.18): old clients expect these fields in JSON
    observation_id: Optional[int] = None
    entity_id: Optional[int] = None
    entity_external_id: Optional[str] = None
    title: Optional[str] = None
    file_path: str
    permalink: str
    category: str
    content: str
    created_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        return dt.isoformat()


class MemoryMetadata(BaseModel):
    """Simplified response metadata."""

    uri: Optional[str] = None
    types: Optional[List[SearchItemType]] = None
    depth: int
    timeframe: Optional[str] = None
    # COMPAT(v0.18): old clients expect generated_at and total_results in JSON
    generated_at: Optional[datetime] = None
    primary_count: Optional[int] = None
    related_count: Optional[int] = None
    total_results: Optional[int] = None
    total_relations: Optional[int] = None
    total_observations: Optional[int] = None

    @field_serializer("generated_at")
    def serialize_generated_at(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


class ContextResult(BaseModel):
    """Context result containing a primary item with its observations and related items."""

    primary_result: Annotated[
        Union[EntitySummary, RelationSummary, ObservationSummary],
        Field(discriminator="type", description="Primary item"),
    ]

    observations: Sequence[ObservationSummary] = Field(
        description="Observations belonging to this entity", default_factory=list
    )

    related_results: Sequence[
        Annotated[
            Union[EntitySummary, RelationSummary, ObservationSummary], Field(discriminator="type")
        ]
    ] = Field(description="Related items", default_factory=list)


class GraphContext(BaseModel):
    """Complete context response."""

    # hierarchical results
    results: Sequence[ContextResult] = Field(
        description="Hierarchical results with related items nested", default_factory=list
    )

    # Context metadata
    metadata: MemoryMetadata

    page: Optional[int] = None
    page_size: Optional[int] = None
    has_more: bool = False
