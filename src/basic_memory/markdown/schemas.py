"""Schema models for entity markdown files."""

from datetime import datetime
from typing import Literal, override, TYPE_CHECKING, Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from basic_memory.markdown.sections import MarkdownSection
from basic_memory.temporal import TemporalAssertion


class Observation(BaseModel):
    """An observation about an entity."""

    category: Optional[str] = "Note"
    content: str
    tags: Optional[List[str]] = None
    context: Optional[str] = None
    # Collection-shaped from day one: the MVP parses at most one qualifier per
    # observation, but carrying several later must not be a schema break (SPEC-82).
    temporal: List[TemporalAssertion] = []
    # Set for the three reported cases: an unknown kind, an unterminated quote, and an
    # unquoted point the one-token rule truncated. Its text stays in `content`, so
    # nothing is dropped -- only the derived temporal projection is withheld until the
    # author fixes the line. Text that simply is not a date sets nothing here; it is
    # ordinary content.
    temporal_error: Optional[str] = None

    @override
    def __str__(self) -> str:
        # Replaying `source_text` verbatim is what makes parse/serialize a byte-exact
        # round trip: `valid_during` holds normalized bounds, the author's text does not.
        qualifiers = " ".join(assertion.source_text for assertion in self.temporal)
        prefix = f"{qualifiers} " if qualifiers else ""
        obs_string = f"- [{self.category}] {prefix}{self.content}"
        if self.context:
            obs_string += f" ({self.context})"
        return obs_string


class Relation(BaseModel):
    """A relation between entities."""

    type: str
    target: str
    context: Optional[str] = None

    @override
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


# What the parser found at the top of the file. "present" is a fenced block
# that parsed as YAML and can be rewritten field by field; "absent" means no
# fences, so frontmatter may be injected; "malformed" is a fenced block that is
# not YAML (a letterhead between horizontal rules, a broken document). The
# indexer must never rewrite a malformed block, because it cannot know which
# bytes the author meant as metadata, and must still index the file (#1451).
type FrontmatterState = Literal["present", "absent", "malformed"]


class EntityMarkdown(BaseModel):
    """Complete entity combining frontmatter, content, and metadata."""

    frontmatter: EntityFrontmatter
    # The parser always sets this; the default only serves hand-built values.
    frontmatter_state: FrontmatterState = "present"
    content: Optional[str] = None
    observations: List[Observation] = []
    relations: List[Relation] = []
    # Structural pass output: present even for graph-silent notes, unlike the
    # semantic observations/relations above.
    sections: List[MarkdownSection] = []

    # created, updated will have values after a read
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
