"""Write note tool for Basic Memory MCP server."""

from typing import List, Union, Optional

from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_put
from basic_memory.mcp.project_session import get_active_project
from basic_memory.schemas import EntityResponse
from basic_memory.schemas.base import Entity
from basic_memory.utils import parse_tags

# Define TagType as a Union that can accept either a string or a list of strings or None
TagType = Union[List[str], str, None]

# Define TagType as a Union that can accept either a string or a list of strings or None
TagType = Union[List[str], str, None]


@mcp.tool(
    description="Create or update a markdown note. Returns a markdown formatted summary of the semantic content.",
)
async def write_note(
    title: str,
    content: str,
    folder: str,
    tags=None,  # Remove type hint completely to avoid schema issues
    project: Optional[str] = None,
) -> str:
    """Write a markdown note to the knowledge base.

    The content can include semantic observations and relations using markdown syntax.
    Relations can be specified either explicitly or through inline wiki-style links:

    Observations format:
        `- [category] Observation text #tag1 #tag2 (optional context)`

        Examples:
        `- [design] Files are the source of truth #architecture (All state comes from files)`
        `- [tech] Using SQLite for storage #implementation`
        `- [note] Need to add error handling #todo`

    Relations format:
        - Explicit: `- relation_type [[Entity]] (optional context)`
        - Inline: Any `[[Entity]]` reference creates a relation

        Examples:
        `- depends_on [[Content Parser]] (Need for semantic extraction)`
        `- implements [[Search Spec]] (Initial implementation)`
        `- This feature extends [[Base Design]] andst uses [[Core Utils]]`

    Args:
        title: The title of the note
        content: Markdown content for the note, can include observations and relations
        folder: the folder where the file should be saved
        tags: Tags to categorize the note. Can be a list of strings, a comma-separated string, or None.
              Note: If passing from external MCP clients, use a string format (e.g. "tag1,tag2,tag3")
        project: Optional project name to write to. If not provided, uses current active project.

    Returns:
        A markdown formatted summary of the semantic content, including:
        - Creation/update status
        - File path and checksum
        - Observation counts by category
        - Relation counts (resolved/unresolved)
        - Tags if present
    """
    logger.info(f"MCP tool call tool=write_note folder={folder}, title={title}, tags={tags}")

    # Process tags using the helper function
    tag_list = parse_tags(tags)
    # Create the entity request
    metadata = {"tags": tag_list} if tag_list else None
    entity = Entity(
        title=title,
        folder=folder,
        entity_type="note",
        content_type="text/markdown",
        content=content,
        entity_metadata=metadata,
    )
    active_project = get_active_project(project)
    project_url = active_project.project_url

    # Create or update via knowledge API
    logger.debug(f"Creating entity via API permalink={entity.permalink}")
    url = f"{project_url}/knowledge/entities/{entity.permalink}"
    response = await call_put(client, url, json=entity.model_dump())
    result = EntityResponse.model_validate(response.json())

    # Format semantic summary based on status code
    action = "Created" if response.status_code == 201 else "Updated"
    summary = [
        f"# {action} note",
        f"file_path: {result.file_path}",
        f"permalink: {result.permalink}",
        f"checksum: {result.checksum[:8] if result.checksum else 'unknown'}",
    ]

    # Count observations by category
    categories = {}
    if result.observations:
        for obs in result.observations:
            categories[obs.category] = categories.get(obs.category, 0) + 1

        summary.append("\n## Observations")
        for category, count in sorted(categories.items()):
            summary.append(f"- {category}: {count}")

    # Count resolved/unresolved relations
    unresolved = 0
    resolved = 0
    if result.relations:
        unresolved = sum(1 for r in result.relations if not r.to_id)
        resolved = len(result.relations) - unresolved

        summary.append("\n## Relations")
        summary.append(f"- Resolved: {resolved}")
        if unresolved:
            summary.append(f"- Unresolved: {unresolved}")
            summary.append("\nUnresolved relations will be retried on next sync.")

    if tag_list:
        summary.append(f"\n## Tags\n- {', '.join(tag_list)}")

    # Log the response with structured data
    logger.info(
        f"MCP tool response: tool=write_note action={action} permalink={result.permalink} observations_count={len(result.observations)} relations_count={len(result.relations)} resolved_relations={resolved} unresolved_relations={unresolved} status_code={response.status_code}"
    )
    return "\n".join(summary)
