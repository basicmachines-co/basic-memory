"""Schemas for memory context."""

from datetime import datetime
from typing import Dict, List, Any, Optional, Annotated

import pydantic
from annotated_types import MinLen, MaxLen
from pydantic import BaseModel, field_validator, Field, BeforeValidator, TypeAdapter, AnyUrl

from basic_memory.schemas.search import SearchItemType


def normalize_memory_url(url: str) -> str:
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
    clean_path = url.removeprefix("memory://")
    return f"memory://{clean_path}"


MemoryUrl = Annotated[
    str,
    BeforeValidator(str.strip),  # Clean whitespace
    MinLen(1),       
    MaxLen(2028),   
]

memory_url = TypeAdapter(MemoryUrl) 

def memory_url_path(url: memory_url) -> str:
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

    permalink: str
    title: str
    file_path: str
    created_at: datetime


class RelationSummary(BaseModel):
    """Simplified relation representation."""

    permalink: str
    type: str
    from_id: str
    to_id: Optional[str] = None


class ObservationSummary(BaseModel):
    """Simplified observation representation."""

    permalink: str
    category: str
    content: str


class MemoryMetadata(BaseModel):
    """Simplified response metadata."""

    uri: Optional[str] = None
    types: Optional[List[SearchItemType]] = None
    depth: int
    timeframe: str
    generated_at: datetime
    total_results: int
    total_relations: int


class GraphContext(BaseModel):
    """Complete context response."""

    # Direct matches
    primary_results: List[EntitySummary | RelationSummary | ObservationSummary] = Field(
        description="results directly matching URI"
    )

    # Related entities
    related_results: List[EntitySummary | RelationSummary | ObservationSummary] = Field(
        description="related results"
    )

    # Context metadata
    metadata: MemoryMetadata