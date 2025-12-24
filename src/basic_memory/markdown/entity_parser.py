"""Parser for markdown files into Entity objects.

Uses markdown-it with plugins to parse structured data from markdown content.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import dateparser
import frontmatter
import yaml
from loguru import logger
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


def normalize_frontmatter_value(value: Any) -> Any:
    """Normalize frontmatter values to safe types for processing.

    PyYAML automatically converts various string-like values into native Python types:
    - Date strings ("2025-10-24") → datetime.date objects
    - Numbers ("1.0") → int or float
    - Booleans ("true") → bool
    - Lists → list objects

    This can cause AttributeError when code expects strings and calls string methods
    like .strip() on these values (see GitHub issue #236).

    This function normalizes all frontmatter values to safe types:
    - Dates/datetimes → ISO format strings
    - Numbers (int/float) → strings
    - Booleans → strings ("True"/"False")
    - Lists → preserved as lists, but items are recursively normalized
    - Dicts → preserved as dicts, but values are recursively normalized
    - Strings → kept as-is
    - None → kept as None

    Args:
        value: The frontmatter value to normalize

    Returns:
        The normalized value safe for string operations

    Example:
        >>> normalize_frontmatter_value(datetime.date(2025, 10, 24))
        '2025-10-24'
        >>> normalize_frontmatter_value([datetime.date(2025, 10, 24), "tag", 123])
        ['2025-10-24', 'tag', '123']
        >>> normalize_frontmatter_value(True)
        'True'
    """
    # Convert date/datetime objects to ISO format strings
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    # Convert boolean to string (must come before int check since bool is subclass of int)
    if isinstance(value, bool):
        return str(value)

    # Convert numbers to strings
    if isinstance(value, (int, float)):
        return str(value)

    # Recursively process lists (preserve as list, normalize items)
    if isinstance(value, list):
        return [normalize_frontmatter_value(item) for item in value]

    # Recursively process dicts (preserve as dict, normalize values)
    if isinstance(value, dict):
        return {key: normalize_frontmatter_value(val) for key, val in value.items()}

    # Keep strings and None as-is
    return value


def normalize_frontmatter_metadata(metadata: dict) -> dict:
    """Normalize all values in frontmatter metadata dict.

    Converts date/datetime objects to ISO format strings to prevent
    AttributeError when code expects strings (GitHub issue #236).

    Args:
        metadata: The frontmatter metadata dictionary

    Returns:
        A new dictionary with all values normalized
    """
    return {key: normalize_frontmatter_value(value) for key, value in metadata.items()}


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
        """Parse markdown content from file stats.

        Delegates to parse_markdown_content() for actual parsing logic.
        Exists for backwards compatibility with code that passes file paths.
        """
        # Extract file stat info for timestamps
        file_stats = absolute_path.stat()

        # Delegate to parse_markdown_content with timestamps from file stats
        return await self.parse_markdown_content(
            file_path=absolute_path,
            content=file_content,
            mtime=file_stats.st_mtime,
            ctime=file_stats.st_ctime,
        )

    async def parse_markdown_content(
        self,
        file_path: Path,
        content: str,
        mtime: Optional[float] = None,
        ctime: Optional[float] = None,
    ) -> EntityMarkdown:
        """Parse markdown content without requiring file to exist on disk.

        Useful for parsing content from S3 or other remote sources where the file
        is not available locally.

        Args:
            file_path: Path for metadata (doesn't need to exist on disk)
            content: Markdown content as string
            mtime: Optional modification time (Unix timestamp)
            ctime: Optional creation time (Unix timestamp)

        Returns:
            EntityMarkdown with parsed content
        """
        # Parse frontmatter with proper error handling for malformed YAML
        try:
            post = frontmatter.loads(content)
        except yaml.YAMLError as e:
            logger.warning(
                f"Failed to parse YAML frontmatter in {file_path}: {e}. "
                f"Treating file as plain markdown without frontmatter."
            )
            post = frontmatter.Post(content, metadata={})

        # Normalize frontmatter values
        metadata = normalize_frontmatter_metadata(post.metadata)

        # Ensure required fields have defaults
        title = metadata.get("title")
        if not title or title == "None":
            metadata["title"] = file_path.stem
        else:
            metadata["title"] = title

        entity_type = metadata.get("type")
        metadata["type"] = entity_type if entity_type is not None else "note"

        tags = parse_tags(metadata.get("tags", []))  # pyright: ignore
        if tags:
            metadata["tags"] = tags

        # Parse content for observations and relations
        entity_frontmatter = EntityFrontmatter(metadata=metadata)
        entity_content = parse(post.content)

        # Use provided timestamps or current time as fallback
        now = datetime.now().astimezone()
        created = datetime.fromtimestamp(ctime).astimezone() if ctime else now
        modified = datetime.fromtimestamp(mtime).astimezone() if mtime else now

        return EntityMarkdown(
            frontmatter=entity_frontmatter,
            content=post.content,
            observations=entity_content.observations,
            relations=entity_content.relations,
            created=created,
            modified=modified,
        )
