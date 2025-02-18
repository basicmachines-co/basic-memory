"""Note management tools for Basic Memory MCP server.

These tools provide a natural interface for working with markdown notes
while leveraging the underlying knowledge graph structure.
"""

from typing import Optional, List

from loguru import logger

from basic_memory.mcp.server import mcp
from basic_memory.mcp.async_client import client
from basic_memory.schemas import EntityResponse, DeleteEntitiesResponse
from basic_memory.schemas.base import Entity
from basic_memory.mcp.tools.utils import call_get, call_put, call_delete
from basic_memory.schemas.memory import memory_url_path


@mcp.tool(
    description="Create or update a markdown note. Returns the permalink for referencing.",
)
async def write_note(
    title: str,
    content: str,
    folder: str,
    tags: Optional[List[str]] = None,
    verbose: bool = False,
) -> EntityResponse | str:
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
        verbose: If True, returns full EntityResponse with semantic info

    Returns:
        If verbose=False: Permalink that can be used to reference the note
        If verbose=True: EntityResponse with full semantic details

    Examples:
        # Note with both explicit and inline relations
        write_note(
            title="Search Implementation",
            content="# Search Component\\n\\n"
                   "Implementation of the search feature, building on [[Core Search]].\\n\\n"
                   "## Observations\\n"
                   "- [tech] Using FTS5 for full-text search #implementation\\n"
                   "- [design] Need pagination support #todo\\n\\n"
                   "## Relations\\n"
                   "- implements [[Search Spec]]\\n"
                   "- depends_on [[Database Schema]]",
            folder="docs/components"
        )

        # Note with tags
        write_note(
            title="Error Handling Design",
            content="# Error Handling\\n\\n"
                   "This design builds on [[Reliability Design]].\\n\\n"
                   "## Approach\\n"
                   "- [design] Use error codes #architecture\\n"
                   "- [tech] Implement retry logic #implementation\\n\\n"
                   "## Relations\\n"
                   "- extends [[Base Error Handling]]",
            folder="docs/design",
            tags=["architecture", "reliability"]
        )
    """
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

    # Use existing knowledge tool
    logger.info(f"Creating {entity.permalink}")
    url = f"/knowledge/entities/{entity.permalink}"
    response = await call_put(client, url, json=entity.model_dump())
    result = EntityResponse.model_validate(response.json())
    return result if verbose else result.permalink


@mcp.tool(description="Read note content by title, permalink, relation, or pattern")
async def read_note(identifier: str) -> str:
    """Get note content in unified diff format.

    The content is returned in a unified diff inspired format:
    ```
    --- memory://docs/example 2025-01-31T19:32:49 7d9f1c8b
    <document content>
    ```

    Multiple documents (from relations or pattern matches) are separated by
    additional headers.

    Args:
        identifier: Can be one of:
            - Note title ("Project Planning")
            - Note permalink ("docs/example")
            - Relation path ("docs/example/depends-on/other-doc")
            - Pattern match ("docs/*-architecture")

    Returns:
        Document content in unified diff format. For single documents, returns
        just that document's content. For relations or pattern matches, returns
        multiple documents separated by unified diff headers.

    Examples:
        # Single document
        content = await read_note("Project Planning")

        # Read by permalink
        content = await read_note("docs/architecture/file-first")

        # Follow relation
        content = await read_note("docs/architecture/depends-on/docs/content-parser")

        # Pattern matching
        content = await read_note("docs/*-architecture")  # All architecture docs
        content = await read_note("docs/*/implements/*")  # Find implementations

    Output format:
        ```
        --- memory://docs/example 2025-01-31T19:32:49 7d9f1c8b
        <first document content>

        --- memory://docs/other 2025-01-30T15:45:22 a1b2c3d4
        <second document content>
        ```

    The headers include:
    - Full memory:// URI for the document
    - Last modified timestamp
    - Content checksum
    """
    logger.info(f"Reading note {identifier}")
    url = memory_url_path(identifier)
    response = await call_get(client, f"/resource/{url}")
    return response.text


@mcp.tool(description="Delete a note by title or permalink")
async def delete_note(identifier: str) -> bool:
    """Delete a note from the knowledge base.

    Args:
        identifier: Note title or permalink

    Returns:
        True if note was deleted, False otherwise

    Examples:
        # Delete by title
        delete_note("Meeting Notes: Project Planning")

        # Delete by permalink
        delete_note("notes/project-planning")
    """
    response = await call_delete(client, f"/knowledge/entities/{identifier}")
    result = DeleteEntitiesResponse.model_validate(response.json())
    return result.deleted
