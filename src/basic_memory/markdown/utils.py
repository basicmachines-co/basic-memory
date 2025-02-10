"""Utilities for converting between markdown and entity models."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Union

from frontmatter import Post

from basic_memory.markdown import EntityMarkdown, EntityFrontmatter, Observation, Relation
from basic_memory.markdown.entity_parser import parse
from basic_memory.models import Entity, ObservationCategory, Observation as ObservationModel
from basic_memory.utils import generate_permalink


def entity_model_to_markdown(entity: Entity, content: Optional[str] = None) -> EntityMarkdown:
    """
    Converts an entity model to its Markdown representation.

    Args:
        entity: Entity model to convert
        content: Optional raw Markdown content to parse for semantic info

    Returns:
        EntityMarkdown representation of the entity
    """
    metadata = entity.entity_metadata or {}
    metadata["type"] = entity.entity_type or "note"
    metadata["title"] = entity.title
    metadata["permalink"] = entity.permalink

    # convert model to markdown
    entity_observations = [
        Observation(
            category=obs.category,
            content=obs.content,
            tags=obs.tags if obs.tags else None,
            context=obs.context,
        )
        for obs in entity.observations
    ]

    entity_relations = [
        Relation(
            type=r.relation_type,
            target=r.to_entity.title if r.to_entity else r.to_name,
            context=r.context,
        )
        for r in entity.outgoing_relations
    ]

    observations = entity_observations
    relations = entity_relations

    # parse the content to see if it has semantic info (observations/relations)
    if content:
        entity_content = parse(content)
        if entity_content:
            # Remove if they are already in the content
            observations = [o for o in entity_observations if o not in entity_content.observations]
            relations = [r for r in entity_relations if r not in entity_content.relations]

            # Remove from the content if not present in the db entity
            for o in entity_content.observations:
                if o not in entity_observations and content:
                    content = content.replace(str(o), "")

            for r in entity_content.relations:
                if r not in entity_relations and content:
                    content = content.replace(str(r), "")

    return EntityMarkdown(
        frontmatter=EntityFrontmatter(metadata=metadata),
        content=content,
        observations=observations,
        relations=relations,
        created=entity.created_at,
        modified=entity.updated_at,
    )


def entity_model_from_markdown(
    file_path: Path,
    markdown: EntityMarkdown,
    entity: Optional[Entity] = None
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
    def get_valid_category(obs: Observation) -> str:
        """Get valid observation category, defaulting to NOTE."""
        if not obs.category or obs.category not in [c.value for c in ObservationCategory]:
            return ObservationCategory.NOTE.value
        return obs.category

    if not markdown.created or not markdown.modified:
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
    metadata = (markdown.frontmatter.metadata or {})
    model.entity_metadata = {
        k: str(v) for k, v in metadata.items()
        if v is not None
    }
    
    # Convert observations
    model.observations = [
        ObservationModel(
            content=obs.content,
            category=get_valid_category(obs),
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
        **frontmatter_metadata
    )
    return post