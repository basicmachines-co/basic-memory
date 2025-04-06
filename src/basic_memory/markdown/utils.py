"""Utilities for converting between markdown and entity models."""

from pathlib import Path
from typing import Any, Optional

from frontmatter import Post

from basic_memory.file_utils import has_frontmatter, remove_frontmatter, parse_frontmatter
from basic_memory.markdown import EntityMarkdown
from basic_memory.models import Entity
from basic_memory.models import Observation as ObservationModel


def entity_model_from_markdown(
    file_path: Path, markdown: EntityMarkdown, entity: Optional[Entity] = None
) -> Entity:
    """
    Convert markdown entity to model. Does not include relations.

    Args:
        file_path: Path to the markdown file
        markdown: Parsed markdown entity
        entity: Optional existing entity to update

    Returns:
        Entity model populated from markdown

    Raises:
        ValueError: If required datetime fields are missing from markdown
    """

    if not markdown.created or not markdown.modified:  # pragma: no cover
        raise ValueError("Both created and modified dates are required in markdown")

    # Create or update entity
    model = entity or Entity()

    # Update basic fields
    model.title = markdown.frontmatter.title
    model.entity_type = markdown.frontmatter.type
    model.permalink = markdown.frontmatter.permalink
    model.file_path = str(file_path)
    model.content_type = "text/markdown"
    model.created_at = markdown.created
    model.updated_at = markdown.modified

    # Handle metadata - ensure all values are strings and filter None
    metadata = markdown.frontmatter.metadata or {}
    model.entity_metadata = {k: str(v) for k, v in metadata.items() if v is not None}

    # Convert observations
    model.observations = [
        ObservationModel(
            content=obs.content,
            category=obs.category,
            context=obs.context,
            tags=obs.tags,
        )
        for obs in markdown.observations
    ]

    return model


async def schema_to_markdown(schema: Any) -> Post:
    """
    Convert schema to markdown Post object.

    Args:
        schema: Schema to convert (must have title, entity_type, and permalink attributes)

    Returns:
        Post object with frontmatter metadata
    """
    # Extract content and metadata
    content = schema.content or ""
    entity_metadata = dict(schema.entity_metadata or {})

    # if the content contains frontmatter, remove it and merge
    if has_frontmatter(content):
        content_frontmatter = parse_frontmatter(content)
        content = remove_frontmatter(content)

        # Merge content frontmatter with entity metadata
        # (entity_metadata takes precedence for conflicts)
        content_frontmatter.update(entity_metadata)
        entity_metadata = content_frontmatter

    # Remove special fields for ordered frontmatter
    for field in ["type", "title", "permalink"]:
        entity_metadata.pop(field, None)

    # Create Post with fields ordered by insert order
    post = Post(
        content,
        title=schema.title,
        type=schema.entity_type,
    )
    # set the permalink if passed in
    if schema.permalink:
        post.metadata["permalink"] = schema.permalink

    if entity_metadata:
        post.metadata.update(entity_metadata)

    return post
