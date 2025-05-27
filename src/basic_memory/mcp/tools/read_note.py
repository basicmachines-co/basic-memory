"""Read note tool for Basic Memory MCP server."""

from textwrap import dedent
from typing import Optional

from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.search import search_notes
from basic_memory.mcp.tools.utils import call_get
from basic_memory.mcp.project_session import get_active_project
from basic_memory.schemas.memory import memory_url_path


@mcp.tool(
    description="Read a markdown note by title or permalink.",
)
async def read_note(
    identifier: str, page: int = 1, page_size: int = 10, project: Optional[str] = None
) -> str:
    """Read a markdown note from the knowledge base.

    This tool finds and retrieves a note by its title, permalink, or content search,
    returning the raw markdown content including observations, relations, and metadata.
    It will try multiple lookup strategies to find the most relevant note.

    Args:
        identifier: The title or permalink of the note to read
                   Can be a full memory:// URL, a permalink, a title, or search text
        page: Page number for paginated results (default: 1)
        page_size: Number of items per page (default: 10)
        project: Optional project name to read from. If not provided, uses current active project.

    Returns:
        The full markdown content of the note if found, or helpful guidance if not found.

    Examples:
        # Read by permalink
        read_note("specs/search-spec")

        # Read by title
        read_note("Search Specification")

        # Read with memory URL
        read_note("memory://specs/search-spec")

        # Read with pagination
        read_note("Project Updates", page=2, page_size=5)

        # Read from specific project
        read_note("Meeting Notes", project="work-project")
    """

    active_project = get_active_project(project)
    project_url = active_project.project_url

    # Get the file via REST API - first try direct permalink lookup
    entity_path = memory_url_path(identifier)
    path = f"{project_url}/resource/{entity_path}"
    logger.info(f"Attempting to read note from URL: {path}")

    try:
        # Try direct lookup first
        response = await call_get(client, path, params={"page": page, "page_size": page_size})

        # If successful, return the content
        if response.status_code == 200:
            logger.info("Returning read_note result from resource: {path}", path=entity_path)
            return response.text
    except Exception as e:  # pragma: no cover
        logger.info(f"Direct lookup failed for '{path}': {e}")
        # Continue to fallback methods

    # Fallback 1: Try title search via API
    logger.info(f"Search title for: {identifier}")
    title_results = await search_notes(query=identifier, search_type="title", project=project)

    if title_results and title_results.results:
        result = title_results.results[0]  # Get the first/best match
        if result.permalink:
            try:
                # Try to fetch the content using the found permalink
                path = f"{project_url}/resource/{result.permalink}"
                response = await call_get(
                    client, path, params={"page": page, "page_size": page_size}
                )

                if response.status_code == 200:
                    logger.info(f"Found note by title search: {result.permalink}")
                    return response.text
            except Exception as e:  # pragma: no cover
                logger.info(
                    f"Failed to fetch content for found title match {result.permalink}: {e}"
                )
    else:
        logger.info(f"No results in title search for: {identifier}")

    # Fallback 2: Text search as a last resort
    logger.info(f"Title search failed, trying text search for: {identifier}")
    text_results = await search_notes(query=identifier, search_type="text", project=project)

    # We didn't find a direct match, construct a helpful error message
    if not text_results or not text_results.results:
        # No results at all
        return format_not_found_message(identifier)
    else:
        # We found some related results
        return format_related_results(identifier, text_results.results[:5])


def format_not_found_message(identifier: str) -> str:
    """Format a helpful message when no note was found."""
    return dedent(f"""
        # Note Not Found: "{identifier}"
        
        I couldn't find any notes matching "{identifier}". Here are some suggestions:
        
        ## Check Identifier Type
        - If you provided a title, try using the exact permalink instead
        - If you provided a permalink, check for typos or try a broader search
        
        ## Search Instead
        Try searching for related content:
        ```
        search_notes(query="{identifier}")
        ```
        
        ## Recent Activity
        Check recently modified notes:
        ```
        recent_activity(timeframe="7d")
        ```
        
        ## Create New Note
        This might be a good opportunity to create a new note on this topic:
        ```
        write_note(
            title="{identifier.capitalize()}",
            content='''
            # {identifier.capitalize()}
            
            ## Overview
            [Your content here]
            
            ## Observations
            - [category] [Observation about {identifier}]
            
            ## Relations
            - relates_to [[Related Topic]]
            ''',
            folder="notes"
        )
        ```
    """)


def format_related_results(identifier: str, results) -> str:
    """Format a helpful message with related results when an exact match wasn't found."""
    message = dedent(f"""
        # Note Not Found: "{identifier}"
        
        I couldn't find an exact match for "{identifier}", but I found some related notes:
        
        """)

    for i, result in enumerate(results):
        message += dedent(f"""
            ## {i + 1}. {result.title}
            - **Type**: {result.type.value}
            - **Permalink**: {result.permalink}
            
            You can read this note with:
            ```
            read_note("{result.permalink}")
            ```
            
            """)

    message += dedent("""
        ## Try More Specific Lookup
        For exact matches, try using the full permalink from one of the results above.
        
        ## Search For More Results
        To see more related content:
        ```
        search_notes(query="{identifier}")
        ```
        
        ## Create New Note
        If none of these match what you're looking for, consider creating a new note:
        ```
        write_note(
            title="[Your title]",
            content="[Your content]",
            folder="notes"
        )
        ```
    """)

    return message
