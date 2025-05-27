"""Parser for markdown files into Entity objects.

Uses markdown-it with plugins to parse structured data from markdown content.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import dateparser
import frontmatter
from markdown_it import MarkdownIt

from basic_memory.markdown.plugins import observation_plugin, relation_plugin
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation,
    Relation,
)
from basic_memory.utils import parse_tags

md = MarkdownIt().use(observation_plugin).use(relation_plugin)


@dataclass
class EntityContent:
    content: str
    observations: list[Observation] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


def parse(content: str) -> EntityContent:
    """Parse markdown content into EntityMarkdown."""

    # Parse content for observations and relations using markdown-it
    observations = []
    relations = []

    if content:
        for token in md.parse(content):
            # check for observations and relations
            if token.meta:
                if "observation" in token.meta:
                    obs = token.meta["observation"]
                    observation = Observation.model_validate(obs)
                    observations.append(observation)
                if "relations" in token.meta:
                    rels = token.meta["relations"]
                    relations.extend([Relation.model_validate(r) for r in rels])

    return EntityContent(
        content=content,
        observations=observations,
        relations=relations,
    )


# def parse_tags(tags: Any) -> list[str]:
#     """Parse tags into list of strings."""
#     if isinstance(tags, (list, tuple)):
#         return [str(t).strip() for t in tags if str(t).strip()]
#     return [t.strip() for t in tags.split(",") if t.strip()]


class EntityParser:
    """Parser for markdown files into Entity objects."""

    def __init__(self, base_path: Path):
        """Initialize parser with base path for relative permalink generation."""
        self.base_path = base_path.resolve()

    def parse_date(self, value: Any) -> Optional[datetime]:
        """Parse date strings using dateparser for maximum flexibility.

        Supports human friendly formats like:
        - 2024-01-15
        - Jan 15, 2024
        - 2024-01-15 10:00 AM
        - yesterday
        - 2 days ago
        """
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = dateparser.parse(value)
            if parsed:
                return parsed
        return None

    async def parse_file(self, path: Path | str) -> EntityMarkdown:
        """Parse markdown file into EntityMarkdown."""

        # Check if the path is already absolute
        if (
            isinstance(path, Path)
            and path.is_absolute()
            or (isinstance(path, str) and Path(path).is_absolute())
        ):
            absolute_path = Path(path)
        else:
            absolute_path = self.get_file_path(path)

        # Parse frontmatter and content using python-frontmatter
        file_content = absolute_path.read_text(encoding="utf-8")
        return await self.parse_file_content(absolute_path, file_content)

    def get_file_path(self, path):
        """Get absolute path for a file using the base path for the project."""
        return self.base_path / path

    async def parse_file_content(self, absolute_path, file_content):
        post = frontmatter.loads(file_content)
        # Extract file stat info
        file_stats = absolute_path.stat()
        metadata = post.metadata
        metadata["title"] = post.metadata.get("title", absolute_path.stem)
        metadata["type"] = post.metadata.get("type", "note")
        tags = parse_tags(post.metadata.get("tags", []))  # pyright: ignore
        if tags:
            metadata["tags"] = tags
        # frontmatter
        entity_frontmatter = EntityFrontmatter(
            metadata=post.metadata,
        )
        entity_content = parse(post.content)
        return EntityMarkdown(
            frontmatter=entity_frontmatter,
            content=post.content,
            observations=entity_content.observations,
            relations=entity_content.relations,
            created=datetime.fromtimestamp(file_stats.st_ctime),
            modified=datetime.fromtimestamp(file_stats.st_mtime),
        )
