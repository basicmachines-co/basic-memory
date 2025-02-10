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
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Annotated, Dict

from annotated_types import MinLen, MaxLen
from dateparser import parse

from pydantic import BaseModel, BeforeValidator, Field, model_validator, ValidationError

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


def validate_path_format(path: str) -> str:
    """Validate path has the correct format: not empty."""
    if not path or not isinstance(path, str):
        raise ValueError("Path must be a non-empty string")

    return path


class ObservationCategory(str, Enum):
    """Categories for structuring observations.

    Categories help organize knowledge and make it easier to find later:
    - tech: Implementation details and technical notes
    - design: Architecture decisions and patterns
    - feature: User-facing capabilities
    - note: General observations (default)
    - issue: Problems or concerns
    - todo: Future work items

    Categories are case-insensitive for easier use.
    """

    TECH = "tech"
    DESIGN = "design"
    FEATURE = "feature"
    NOTE = "note"
    ISSUE = "issue"
    TODO = "todo"

    @classmethod
    def _missing_(cls, value: object) -> Optional["ObservationCategory"]:
        """Handle case-insensitive lookup."""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return cls(value.lower())
            except ValueError:
                return None
        return None


def validate_timeframe(timeframe: str) -> str:
    """Convert human readable timeframes to a duration relative to the current time."""
    if not isinstance(timeframe, str):
        raise ValueError("Timeframe must be a string")

    # Parse relative time expression
    parsed = parse(timeframe)
    if not parsed:
        raise ValueError(f"Could not parse timeframe: {timeframe}")

    # Convert to duration
    now = datetime.now()
    if parsed > now:
        raise ValueError("Timeframe cannot be in the future")

    # Could format the duration back to our standard format
    days = (now - parsed).days

    # Could enforce reasonable limits
    if days > 365:
        raise ValueError("Timeframe should be <= 1 year")

    return f"{days}d"


TimeFrame = Annotated[
    str,
    BeforeValidator(validate_timeframe)
]

PathId = Annotated[str, BeforeValidator(validate_path_format)]
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

    category: ObservationCategory
    content: ObservationStr
    tags: Optional[List[str]] = Field(default_factory=list)
    context: Optional[str] = None

class Relation(BaseModel):
    """Represents a directed edge between entities in the knowledge graph.

    Relations are directed connections stored in active voice (e.g., "created", "depends_on").
    The from_permalink represents the source or actor entity, while to_permalink represents the target
    or recipient entity.
    """

    from_id: PathId
    to_id: PathId
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
        examples=["text/markdown", "image/jpeg"], default="text/markdown"
    )

    @property
    def file_path(self):
        """Get the file path for this entity based on its permalink."""
        return f"{self.folder}/{self.title}.md" if self.folder else f"{self.title}.md"
    
    @property
    def permalink(self) -> PathId:
        """Get a url friendly path}."""
        return self._permalink or generate_permalink(self.file_path)

    @model_validator(mode="after")
    @classmethod
    def infer_content_type(cls, entity: "Entity") -> Dict | None:
        """Infer content_type from file_path if not provided."""
        if not entity.content_type:
            path = Path(entity.file_path)
            if not path.exists():
                return None
            mime_type, _ = mimetypes.guess_type(path.name)
            entity.content_type = mime_type or "text/plain"

        return entity
