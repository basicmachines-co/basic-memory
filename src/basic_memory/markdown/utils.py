"""Utilities for converting between markdown and entity models."""

from pathlib import Path
from typing import Optional, Any

from frontmatter import Post

from basic_memory.markdown import EntityMarkdown
from basic_memory.models import Entity, Observation as ObservationModel
from basic_memory.utils import generate_permalink


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

    # Generate permalink if not provided
    permalink = markdown.frontmatter.permalink or generate_permalink(file_path)

    # Create or update entity
    model = entity or Entity()

    # Update basic fields
    model.title = markdown.frontmatter.title
    model.entity_type = markdown.frontmatter.type
    model.permalink = permalink
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
    frontmatter_metadata = dict(schema.entity_metadata or {})

    # Remove special fields for ordered frontmatter
    for field in ["type", "title", "permalink"]:
        frontmatter_metadata.pop(field, None)

    # Create Post with ordered fields
    post = Post(
        content,
        title=schema.title,
        type=schema.entity_type,
        permalink=schema.permalink,
        **frontmatter_metadata,
    )
    return post
