"""Write note tool for Basic Memory MCP server."""

from typing import List, Union, Optional

from loguru import logger

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project, add_project_metadata
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_put
from basic_memory.schemas import EntityResponse
from fastmcp import Context
from basic_memory.schemas.base import Entity
from basic_memory.utils import parse_tags, validate_project_path

# Define TagType as a Union that can accept either a string or a list of strings or None
TagType = Union[List[str], str, None]


@mcp.tool(
    description="Create or update a markdown note. Returns a markdown formatted summary of the semantic content.",
)
async def write_note(
    title: str,
    content: str,
    folder: str,
    project: Optional[str] = None,
    tags: list[str] | str | None = None,
    note_type: str = "note",
    context: Context | None = None,
) -> str:
    """Write a markdown note to the knowledge base.

    Creates or updates a markdown note with semantic observations and relations.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    The content can include semantic observations and relations using markdown syntax:

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
        folder: Folder path relative to project root where the file should be saved.
                Use forward slashes (/) as separators. Use "/" or "" to write to project root.
                Examples: "notes", "projects/2025", "research/ml", "/" (root)
        project: Project name to write to. Optional - server will resolve using the
                hierarchy above. If unknown, use list_memory_projects() to discover
                available projects.
        tags: Tags to categorize the note. Can be a list of strings, a comma-separated string, or None.
              Note: If passing from external MCP clients, use a string format (e.g. "tag1,tag2,tag3")
        note_type: Type of note to create (stored in frontmatter). Defaults to "note".
                   Can be "guide", "report", "config", "person", etc.
        context: Optional FastMCP context for performance caching.

    Returns:
        A markdown formatted summary of the semantic content, including:
        - Creation/update status with project name
        - File path and checksum
        - Observation counts by category
        - Relation counts (resolved/unresolved)
        - Tags if present
        - Session tracking metadata for project awareness

    Examples:
        # Assistant flow when project is unknown
        # 1. list_memory_projects() -> Ask user which project
        # 2. User: "Use my-research"
        # 3. write_note(...) and remember "my-research" for session

        # Create a simple note
        write_note(
            project="my-research",
            title="Meeting Notes",
            folder="meetings",
            content="# Weekly Standup\\n\\n- [decision] Use SQLite for storage #tech"
        )

        # Create a note with tags and note type
        write_note(
            project="work-project",
            title="API Design",
            folder="specs",
            content="# REST API Specification\\n\\n- implements [[Authentication]]",
            tags=["api", "design"],
            note_type="guide"
        )

        # Update existing note (same title/folder)
        write_note(
            project="my-research",
            title="Meeting Notes",
            folder="meetings",
            content="# Weekly Standup\\n\\n- [decision] Use PostgreSQL instead #tech"
        )

    Raises:
        HTTPError: If project doesn't exist or is inaccessible
        SecurityError: If folder path attempts path traversal
    """
    async with get_client() as client:
        logger.info(
            f"MCP tool call tool=write_note project={project} folder={folder}, title={title}, tags={tags}"
        )

        # Get and validate the project (supports optional project parameter)
        active_project = await get_active_project(client, project, context)

        # Normalize "/" to empty string for root folder (must happen before validation)
        if folder == "/":
            folder = ""

        # Validate folder path to prevent path traversal attacks
        project_path = active_project.home
        if folder and not validate_project_path(folder, project_path):
            logger.warning(
                "Attempted path traversal attack blocked",
                folder=folder,
                project=active_project.name,
            )
            return f"# Error\n\nFolder path '{folder}' is not allowed - paths must stay within project boundaries"

        # Process tags using the helper function
        tag_list = parse_tags(tags)
        # Create the entity request
        metadata = {"tags": tag_list} if tag_list else None
        entity = Entity(
            title=title,
            folder=folder,
            entity_type=note_type,
            content_type="text/markdown",
            content=content,
            entity_metadata=metadata,
        )
        project_url = active_project.permalink

        # Create or update via knowledge API
        logger.debug(f"Creating entity via API permalink={entity.permalink}")
        url = f"{project_url}/knowledge/entities/{entity.permalink}"
        response = await call_put(client, url, json=entity.model_dump())
        result = EntityResponse.model_validate(response.json())

        # Format semantic summary based on status code
        action = "Created" if response.status_code == 201 else "Updated"
        summary = [
            f"# {action} note",
            f"project: {active_project.name}",
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
                summary.append(
                    "\nNote: Unresolved relations point to entities that don't exist yet."
                )
                summary.append(
                    "They will be automatically resolved when target entities are created or during sync operations."
                )

        if tag_list:
            summary.append(f"\n## Tags\n- {', '.join(tag_list)}")

        # Log the response with structured data
        logger.info(
            f"MCP tool response: tool=write_note project={active_project.name} action={action} permalink={result.permalink} observations_count={len(result.observations)} relations_count={len(result.relations)} resolved_relations={resolved} unresolved_relations={unresolved} status_code={response.status_code}"
        )
        result = "\n".join(summary)
        return add_project_metadata(result, active_project.name)
