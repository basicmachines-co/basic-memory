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

import mimetypes
import re
from datetime import datetime, time, timezone
from pathlib import Path
from typing import List, Optional, Annotated, Dict

from annotated_types import MinLen, MaxLen
from dateparser import parse

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from basic_memory.config import ConfigManager
from basic_memory.file_utils import sanitize_for_filename
from basic_memory.utils import generate_permalink


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

    Args:
        timeframe: Natural language timeframe like 'today', '1d', '1 week ago', etc.

    Returns:
        datetime: The parsed datetime for the start of the timeframe, timezone-aware in local system timezone

    Examples:
        parse_timeframe('today') -> 2025-06-05 00:00:00-07:00 (start of today with local timezone)
        parse_timeframe('1d') -> 2025-06-04 14:50:00-07:00 (24 hours ago with local timezone)
        parse_timeframe('1 week ago') -> 2025-05-29 14:50:00-07:00 (1 week ago with local timezone)
    """
    if timeframe.lower() == "today":
        # Return start of today (00:00:00) in local timezone
        naive_dt = datetime.combine(datetime.now().date(), time.min)
        return naive_dt.astimezone()
    else:
        # Use dateparser for other formats
        parsed = parse(timeframe)
        if not parsed:
            raise ValueError(f"Could not parse timeframe: {timeframe}")
        
        # If the parsed datetime is naive, make it timezone-aware in local system timezone
        if parsed.tzinfo is None:
            return parsed.astimezone()
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
            fixed_title = generate_permalink(file_path=fixed_title, split_extension=False)

        return fixed_title

    @property
    def file_path(self):
        """Get the file path for this entity based on its permalink."""
        safe_title = self.safe_title
        if self.content_type == "text/markdown":
            return f"{self.folder}/{safe_title}.md" if self.folder else f"{safe_title}.md"
        else:
            return f"{self.folder}/{safe_title}" if self.folder else safe_title

    @property
    def permalink(self) -> Permalink:
        """Get a url friendly path}."""
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
