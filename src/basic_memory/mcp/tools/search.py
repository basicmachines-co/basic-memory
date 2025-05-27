"""Search tools for Basic Memory MCP server."""

from typing import List, Optional

from loguru import logger

from basic_memory.mcp.async_client import client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.mcp.project_session import get_active_project
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchResponse


@mcp.tool(
    description="Search across all content in the knowledge base.",
)
async def search_notes(
    query: str,
    page: int = 1,
    page_size: int = 10,
    search_type: str = "text",
    types: Optional[List[str]] = None,
    entity_types: Optional[List[str]] = None,
    after_date: Optional[str] = None,
    project: Optional[str] = None,
) -> SearchResponse:
    """Search across all content in the knowledge base.

    This tool searches the knowledge base using full-text search, pattern matching,
    or exact permalink lookup. It supports filtering by content type, entity type,
    and date.

    Args:
        query: The search query string
        page: The page number of results to return (default 1)
        page_size: The number of results to return per page (default 10)
        search_type: Type of search to perform, one of: "text", "title", "permalink" (default: "text")
        types: Optional list of note types to search (e.g., ["note", "person"])
        entity_types: Optional list of entity types to filter by (e.g., ["entity", "observation"])
        after_date: Optional date filter for recent content (e.g., "1 week", "2d")
        project: Optional project name to search in. If not provided, uses current active project.

    Returns:
        SearchResponse with results and pagination info

    Examples:
        # Basic text search
        results = await search_notes("project planning")

        # Boolean AND search (both terms must be present)
        results = await search_notes("project AND planning")

        # Boolean OR search (either term can be present)
        results = await search_notes("project OR meeting")

        # Boolean NOT search (exclude terms)
        results = await search_notes("project NOT meeting")

        # Boolean search with grouping
        results = await search_notes("(project OR planning) AND notes")

        # Search with type filter
        results = await search_notes(
            query="meeting notes",
            types=["entity"],
        )

        # Search with entity type filter, e.g., note vs
        results = await search_notes(
            query="meeting notes",
            types=["entity"],
        )

        # Search for recent content
        results = await search_notes(
            query="bug report",
            after_date="1 week"
        )

        # Pattern matching on permalinks
        results = await search_notes(
            query="docs/meeting-*",
            search_type="permalink"
        )

        # Search in specific project
        results = await search_notes("meeting notes", project="work-project")
    """
    # Create a SearchQuery object based on the parameters
    search_query = SearchQuery()

    # Set the appropriate search field based on search_type
    if search_type == "text":
        search_query.text = query
    elif search_type == "title":
        search_query.title = query
    elif search_type == "permalink" and "*" in query:
        search_query.permalink_match = query
    elif search_type == "permalink":
        search_query.permalink = query
    else:
        search_query.text = query  # Default to text search

    # Add optional filters if provided
    if entity_types:
        search_query.entity_types = [SearchItemType(t) for t in entity_types]
    if types:
        search_query.types = types
    if after_date:
        search_query.after_date = after_date

    active_project = get_active_project(project)
    project_url = active_project.project_url

    logger.info(f"Searching for {search_query}")
    response = await call_post(
        client,
        f"{project_url}/search/",
        json=search_query.model_dump(),
        params={"page": page, "page_size": page_size},
    )
    return SearchResponse.model_validate(response.json())
