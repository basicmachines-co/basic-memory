"""Schema models for entity markdown files."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from pydantic import BaseModel, Field, model_validator


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

    if TYPE_CHECKING:
        # Frontmatter may be built from raw YAML keys. The validator below
        # gathers those keys into the metadata mapping used at runtime.
        def __init__(self, **data: Any) -> None: ...

    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def collect_metadata(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if "metadata" not in data:
            return {"metadata": data}

        metadata = data.get("metadata") or {}
        extras = {key: value for key, value in data.items() if key != "metadata"}
        if extras:
            return {"metadata": {**extras, **metadata}}
        return data

    @property
    def tags(self) -> List[str]:
        tags = self.metadata.get("tags")
        return [str(tag) for tag in tags] if isinstance(tags, list) else []

    @property
    def title(self) -> str:
        title = self.metadata.get("title")
        return title if isinstance(title, str) else ""

    @property
    def type(self) -> str:
        note_type = self.metadata.get("type", "note")
        return note_type if isinstance(note_type, str) else "note"

    @property
    def permalink(self) -> Optional[str]:
        permalink = self.metadata.get("permalink")
        return permalink if isinstance(permalink, str) else None


class EntityMarkdown(BaseModel):
    """Complete entity combining frontmatter, content, and metadata."""

    frontmatter: EntityFrontmatter
    content: Optional[str] = None
    observations: List[Observation] = []
    relations: List[Relation] = []

    # created, updated will have values after a read
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
