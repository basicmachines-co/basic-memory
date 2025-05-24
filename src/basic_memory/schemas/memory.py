"""Schemas for memory context."""

from datetime import datetime
from typing import List, Optional, Annotated, Sequence

from annotated_types import MinLen, MaxLen
from pydantic import BaseModel, Field, BeforeValidator, TypeAdapter

from basic_memory.schemas.search import SearchItemType


def normalize_memory_url(url: str | None) -> str:
    """Normalize a MemoryUrl string.

    Args:
        url: A path like "specs/search" or "memory://specs/search"

    Returns:
        Normalized URL starting with memory://

    Examples:
        >>> normalize_memory_url("specs/search")
        'memory://specs/search'
        >>> normalize_memory_url("memory://specs/search")
        'memory://specs/search'
    """
    if not url:
        return ""

    clean_path = url.removeprefix("memory://")
    return f"memory://{clean_path}"


MemoryUrl = Annotated[
    str,
    BeforeValidator(str.strip),  # Clean whitespace
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

    type: str = "entity"
    permalink: Optional[str]
    title: str
    content: Optional[str] = None
    file_path: str
    created_at: datetime


class RelationSummary(BaseModel):
    """Simplified relation representation."""

    type: str = "relation"
    title: str
    file_path: str
    permalink: str
    relation_type: str
    from_entity: str
    to_entity: Optional[str] = None
    created_at: datetime


class ObservationSummary(BaseModel):
    """Simplified observation representation."""

    type: str = "observation"
    title: str
    file_path: str
    permalink: str
    category: str
    content: str
    created_at: datetime


class MemoryMetadata(BaseModel):
    """Simplified response metadata."""

    uri: Optional[str] = None
    types: Optional[List[SearchItemType]] = None
    depth: int
    timeframe: Optional[str] = None
    generated_at: datetime
    primary_count: Optional[int] = None  # Changed field name
    related_count: Optional[int] = None  # Changed field name
    total_results: Optional[int] = None  # For backward compatibility
    total_relations: Optional[int] = None
    total_observations: Optional[int] = None


class ContextResult(BaseModel):
    """Context result containing a primary item with its observations and related items."""

    primary_result: EntitySummary | RelationSummary | ObservationSummary = Field(
        description="Primary item"
    )

    observations: Sequence[ObservationSummary] = Field(
        description="Observations belonging to this entity", default_factory=list
    )

    related_results: Sequence[EntitySummary | RelationSummary | ObservationSummary] = Field(
        description="Related items", default_factory=list
    )


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
