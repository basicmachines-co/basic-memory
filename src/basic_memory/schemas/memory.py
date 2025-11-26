"""Schemas for memory context."""

from datetime import datetime
from typing import List, Optional, Annotated, Sequence, Literal, Union, Dict

from annotated_types import MinLen, MaxLen
from pydantic import BaseModel, Field, BeforeValidator, TypeAdapter, field_serializer

from basic_memory.schemas.search import SearchItemType


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


def memory_url_path(url: memory_url) -> str:  # pyright: ignore
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
    entity_id: int  # Database ID for v2 API consistency
    permalink: Optional[str]
    title: str
    content: Optional[str] = None
    file_path: str
    created_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        return dt.isoformat()


class RelationSummary(BaseModel):
    """Simplified relation representation."""

    type: Literal["relation"] = "relation"
    relation_id: int  # Database ID for v2 API consistency
    entity_id: Optional[int] = None  # ID of the entity this relation belongs to
    title: str
    file_path: str
    permalink: str
    relation_type: str
    from_entity: Optional[str] = None
    from_entity_id: Optional[int] = None  # ID of source entity
    to_entity: Optional[str] = None
    to_entity_id: Optional[int] = None  # ID of target entity
    created_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        return dt.isoformat()


class ObservationSummary(BaseModel):
    """Simplified observation representation."""

    type: Literal["observation"] = "observation"
    observation_id: int  # Database ID for v2 API consistency
    entity_id: Optional[int] = None  # ID of the entity this observation belongs to
    title: str
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
    generated_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]
    primary_count: Optional[int] = None  # Changed field name
    related_count: Optional[int] = None  # Changed field name
    total_results: Optional[int] = None  # For backward compatibility
    total_relations: Optional[int] = None
    total_observations: Optional[int] = None

    @field_serializer("generated_at")
    def serialize_generated_at(self, dt: datetime) -> str:
        return dt.isoformat()


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


class ActivityStats(BaseModel):
    """Statistics about activity across all projects."""

    total_projects: int
    active_projects: int = Field(description="Projects with activity in timeframe")
    most_active_project: Optional[str] = None
    total_items: int = Field(description="Total items across all projects")
    total_entities: int = 0
    total_relations: int = 0
    total_observations: int = 0


class ProjectActivity(BaseModel):
    """Activity summary for a single project."""

    project_name: str
    project_path: str
    activity: GraphContext = Field(description="The actual activity data for this project")
    item_count: int = Field(description="Total items in this project's activity")
    last_activity: Optional[
        Annotated[datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})]
    ] = Field(default=None, description="Most recent activity timestamp")
    active_folders: List[str] = Field(default_factory=list, description="Most active folders")

    @field_serializer("last_activity")
    def serialize_last_activity(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


class ProjectActivitySummary(BaseModel):
    """Summary of activity across all projects."""

    projects: Dict[str, ProjectActivity] = Field(
        description="Activity per project, keyed by project name"
    )
    summary: ActivityStats
    timeframe: str = Field(description="The timeframe used for the query")
    generated_at: Annotated[
        datetime, Field(json_schema_extra={"type": "string", "format": "date-time"})
    ]
    guidance: Optional[str] = Field(
        default=None, description="Assistant guidance for project selection and session management"
    )

    @field_serializer("generated_at")
    def serialize_generated_at(self, dt: datetime) -> str:
        return dt.isoformat()
