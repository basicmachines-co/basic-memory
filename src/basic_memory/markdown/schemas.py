"""Schema models for entity markdown files."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class Observation(BaseModel):
    """An observation about an entity."""

    category: Optional[str] = "Note"
    content: str
    tags: Optional[List[str]] = None
    context: Optional[str] = None

    def __str__(self) -> str:
        obs_string = f"- [{self.category}] {self.content}"
        if self.context:
            obs_string += f" ({self.context})"
        return obs_string


class Relation(BaseModel):
    """A relation between entities."""

    type: str
    target: str
    context: Optional[str] = None

    def __str__(self) -> str:
        rel_string = f"- {self.type} [[{self.target}]]"
        if self.context:
            rel_string += f" ({self.context})"
        return rel_string


class EntityFrontmatter(BaseModel):
    """Required frontmatter fields for an entity."""

    metadata: dict = {}

    @property
    def tags(self) -> List[str]:
        return self.metadata.get("tags") if self.metadata else None  # pyright: ignore

    @property
    def title(self) -> str:
        return self.metadata.get("title") if self.metadata else None  # pyright: ignore

    @property
    def type(self) -> str:
        return self.metadata.get("type", "note") if self.metadata else "note"  # pyright: ignore

    @property
    def permalink(self) -> str:
        return self.metadata.get("permalink") if self.metadata else None  # pyright: ignore


class EntityMarkdown(BaseModel):
    """Complete entity combining frontmatter, content, and metadata."""

    frontmatter: EntityFrontmatter
    content: Optional[str] = None
    observations: List[Observation] = []
    relations: List[Relation] = []

    # created, updated will have values after a read
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
