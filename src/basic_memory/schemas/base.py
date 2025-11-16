"""Core pydantic models for basic-memory entities, observations, and relations.

This module defines the foundational data structures for the knowledge graph system.
The graph consists of entities (nodes) connected by relations (edges), where each
entity can have multiple observations (facts) attached to it.

Key Concepts:
1. Entities are nodes storing factual observations
2. Relations are directed edges between entities using active voice verbs
3. Observations are atomic facts/notes about an entity
4. Everything is stored in both SQLite and markdown files
"""

import os
import mimetypes
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Annotated, Dict

from annotated_types import MinLen, MaxLen
from dateparser import parse

from pydantic import BaseModel, BeforeValidator, Field, model_validator, computed_field

from basic_memory.config import ConfigManager
from basic_memory.file_utils import sanitize_for_filename, sanitize_for_folder
from basic_memory.utils import generate_permalink


def has_valid_file_extension(filename: str) -> bool:
    """Check if a filename has a valid file extension recognized by mimetypes.

    This is used to determine whether to split the extension when processing
    titles in kebab_filenames mode. Prevents treating periods in version numbers
    or decimals as file extensions.

    Args:
        filename: The filename to check

    Returns:
        True if the filename has a recognized file extension, False otherwise

    Examples:
        >>> has_valid_file_extension("document.md")
        True
        >>> has_valid_file_extension("Version 2.0.0")
        False
        >>> has_valid_file_extension("image.png")
        True
    """
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type is not None


def to_snake_case(name: str) -> str:
    """Convert a string to snake_case.

    Examples:
        BasicMemory -> basic_memory
        Memory Service -> memory_service
        memory-service -> memory_service
        Memory_Service -> memory_service
    """
    name = name.strip()

    # Replace spaces and hyphens and . with underscores
    s1 = re.sub(r"[\s\-\\.]", "_", name)

    # Insert underscore between camelCase
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)

    # Convert to lowercase
    return s2.lower()


def parse_timeframe(timeframe: str) -> datetime:
    """Parse timeframe with special handling for 'today' and other natural language expressions.

    Enforces a minimum 1-day lookback to handle timezone differences in distributed deployments.

    Args:
        timeframe: Natural language timeframe like 'today', '1d', '1 week ago', etc.

    Returns:
        datetime: The parsed datetime for the start of the timeframe, timezone-aware in local system timezone
                 Always returns at least 1 day ago to handle timezone differences.

    Examples:
        parse_timeframe('today') -> 2025-06-04 14:50:00-07:00 (1 day ago, not start of today)
        parse_timeframe('1h') -> 2025-06-04 14:50:00-07:00 (1 day ago, not 1 hour ago)
        parse_timeframe('1d') -> 2025-06-04 14:50:00-07:00 (24 hours ago with local timezone)
        parse_timeframe('1 week ago') -> 2025-05-29 14:50:00-07:00 (1 week ago with local timezone)
    """
    if timeframe.lower() == "today":
        # For "today", return 1 day ago to ensure we capture recent activity across timezones
        # This handles the case where client and server are in different timezones
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)
        return one_day_ago.astimezone()
    else:
        # Use dateparser for other formats
        parsed = parse(timeframe)
        if not parsed:
            raise ValueError(f"Could not parse timeframe: {timeframe}")

        # If the parsed datetime is naive, make it timezone-aware in local system timezone
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        else:
            parsed = parsed

        # Enforce minimum 1-day lookback to handle timezone differences
        # This ensures we don't miss recent activity due to client/server timezone mismatches
        now = datetime.now().astimezone()
        one_day_ago = now - timedelta(days=1)

        # If the parsed time is more recent than 1 day ago, use 1 day ago instead
        if parsed > one_day_ago:
            return one_day_ago
        else:
            return parsed


def validate_timeframe(timeframe: str) -> str:
    """Convert human readable timeframes to a duration relative to the current time."""
    if not isinstance(timeframe, str):
        raise ValueError("Timeframe must be a string")

    # Preserve special timeframe strings that need custom handling
    special_timeframes = ["today"]
    if timeframe.lower() in special_timeframes:
        return timeframe.lower()

    # Parse relative time expression using our enhanced parser
    parsed = parse_timeframe(timeframe)

    # Convert to duration
    now = datetime.now().astimezone()
    if parsed > now:
        raise ValueError("Timeframe cannot be in the future")

    # Could format the duration back to our standard format
    days = (now - parsed).days

    # Could enforce reasonable limits
    if days > 365:
        raise ValueError("Timeframe should be <= 1 year")

    return f"{days}d"


TimeFrame = Annotated[str, BeforeValidator(validate_timeframe)]

Permalink = Annotated[str, MinLen(1)]
"""Unique identifier in format '{path}/{normalized_name}'."""


EntityType = Annotated[str, BeforeValidator(to_snake_case), MinLen(1), MaxLen(200)]
"""Classification of entity (e.g., 'person', 'project', 'concept'). """

ALLOWED_CONTENT_TYPES = {
    "text/markdown",
    "text/plain",
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
}

ContentType = Annotated[
    str,
    BeforeValidator(str.lower),
    Field(pattern=r"^[\w\-\+\.]+/[\w\-\+\.]+$"),
    Field(json_schema_extra={"examples": list(ALLOWED_CONTENT_TYPES)}),
]


RelationType = Annotated[str, MinLen(1), MaxLen(200)]
"""Type of relationship between entities. Always use active voice present tense."""

ObservationStr = Annotated[
    str,
    BeforeValidator(str.strip),  # Clean whitespace
    MinLen(1),  # Ensure non-empty after stripping
    MaxLen(1000),  # Keep reasonable length
]


class Observation(BaseModel):
    """A single observation with category, content, and optional context."""

    category: Optional[str] = None
    content: ObservationStr
    tags: Optional[List[str]] = Field(default_factory=list)
    context: Optional[str] = None


class Relation(BaseModel):
    """Represents a directed edge between entities in the knowledge graph.

    Relations are directed connections stored in active voice (e.g., "created", "depends_on").
    The from_permalink represents the source or actor entity, while to_permalink represents the target
    or recipient entity.
    """

    from_id: Permalink
    to_id: Permalink
    relation_type: RelationType
    context: Optional[str] = None


class Entity(BaseModel):
    """Represents a node in our knowledge graph - could be a person, project, concept, etc.

    Each entity has:
    - A file path (e.g., "people/jane-doe.md")
    - An entity type (for classification)
    - A list of observations (facts/notes about the entity)
    - Optional relations to other entities
    - Optional description for high-level overview
    """

    # private field to override permalink
    # Use empty string "" as sentinel to indicate permalinks are explicitly disabled
    _permalink: Optional[str] = None

    title: str
    content: Optional[str] = None
    folder: str
    entity_type: EntityType = "note"
    entity_metadata: Optional[Dict] = Field(default=None, description="Optional metadata")
    content_type: ContentType = Field(
        description="MIME type of the content (e.g. text/markdown, image/jpeg)",
        examples=["text/markdown", "image/jpeg"],
        default="text/markdown",
    )

    def __init__(self, **data):
        data["folder"] = sanitize_for_folder(data.get("folder", ""))
        super().__init__(**data)

    @property
    def safe_title(self) -> str:
        """
        A sanitized version of the title, which is safe for use on the filesystem. For example,
        a title of "Coupon Enable/Disable Feature" should create a the file as "Coupon Enable-Disable Feature.md"
        instead of creating a file named "Disable Feature.md" beneath the "Coupon Enable" directory.

        Replaces POSIX and/or Windows style slashes as well as a few other characters that are not safe for filenames.
        If kebab_filenames is True, then behavior is consistent with transformation used when generating permalink
        strings (e.g. "Coupon Enable/Disable Feature" -> "coupon-enable-disable-feature").
        """
        fixed_title = sanitize_for_filename(self.title)

        app_config = ConfigManager().config
        use_kebab_case = app_config.kebab_filenames

        if use_kebab_case:
            # Convert to kebab-case: lowercase with hyphens, preserving periods in version numbers
            # generate_permalink() uses mimetypes to detect real file extensions and only splits
            # them off, avoiding misinterpreting periods in version numbers as extensions
            has_extension = has_valid_file_extension(fixed_title)
            fixed_title = generate_permalink(file_path=fixed_title, split_extension=has_extension)

        return fixed_title

    @computed_field
    @property
    def file_path(self) -> str:
        """Get the file path for this entity based on its permalink."""
        safe_title = self.safe_title
        if self.content_type == "text/markdown":
            return (
                os.path.join(self.folder, f"{safe_title}.md") if self.folder else f"{safe_title}.md"
            )
        else:
            return os.path.join(self.folder, safe_title) if self.folder else safe_title

    @property
    def permalink(self) -> Optional[Permalink]:
        """Get a url friendly path}."""
        # Empty string is a sentinel value indicating permalinks are disabled
        if self._permalink == "":
            return None
        return self._permalink or generate_permalink(self.file_path)

    @model_validator(mode="after")
    def infer_content_type(self) -> "Entity":  # pragma: no cover
        if not self.content_type:
            path = Path(self.file_path)
            if not path.exists():
                self.content_type = "text/plain"
            else:
                mime_type, _ = mimetypes.guess_type(path.name)
                self.content_type = mime_type or "text/plain"
        return self
