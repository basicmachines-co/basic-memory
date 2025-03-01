"""Write note tool for Basic Memory MCP server."""

from typing import Optional, List

import logfire
from loguru import logger

from basic_memory.mcp.server import mcp
from basic_memory.mcp.async_client import client
from basic_memory.schemas.base import Entity
from basic_memory.schemas import EntityResponse
from basic_memory.mcp.tools.utils import call_put


@mcp.tool(
    description="Create or update a markdown note. Returns a markdown formatted summary of the semantic content.",
)
async def write_note(
    title: str,
    content: str,
    folder: str,
    tags: Optional[List[str]] = None,
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
        `- This feature extends [[Base Design]] and uses [[Core Utils]]`

    Args:
        title: The title of the note
        content: Markdown content for the note, can include observations and relations
        folder: the folder where the file should be saved
        tags: Optional list of tags to categorize the note

    Returns:
        A markdown formatted summary of the semantic content, including:
        - Creation/update status
        - File path and checksum
        - Observation counts by category
        - Relation counts (resolved/unresolved)
        - Tags if present
    """
    with logfire.span("Writing note", title=title, folder=folder):  # pyright: ignore [reportGeneralTypeIssues]
        logger.info(f"Writing note folder:'{folder}' title: '{title}'")

        # Create the entity request
        metadata = {"tags": [f"#{tag}" for tag in tags]} if tags else None
        entity = Entity(
            title=title,
            folder=folder,
            entity_type="note",
            content_type="text/markdown",
            content=content,
            entity_metadata=metadata,
        )

        # Create or update via knowledge API
        logger.info(f"Creating {entity.permalink}")
        url = f"/knowledge/entities/{entity.permalink}"
        response = await call_put(client, url, json=entity.model_dump())
        result = EntityResponse.model_validate(response.json())

        # Format semantic summary based on status code
        action = "Created" if response.status_code == 201 else "Updated"
        summary = [
            f"# {action} {result.file_path} ({result.checksum[:8] if result.checksum else 'unknown'})",
            f"permalink: {result.permalink}",
        ]

        if result.observations:
            categories = {}
            for obs in result.observations:
                categories[obs.category] = categories.get(obs.category, 0) + 1

            summary.append("\n## Observations")
            for category, count in sorted(categories.items()):
                summary.append(f"- {category}: {count}")

        if result.relations:
            unresolved = sum(1 for r in result.relations if not r.to_id)
            resolved = len(result.relations) - unresolved

            summary.append("\n## Relations")
            summary.append(f"- Resolved: {resolved}")
            if unresolved:
                summary.append(f"- Unresolved: {unresolved}")
                summary.append("\nUnresolved relations will be retried on next sync.")

        if tags:
            summary.append(f"\n## Tags\n- {', '.join(tags)}")

        return "\n".join(summary)
