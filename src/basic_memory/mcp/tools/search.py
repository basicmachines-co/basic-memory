"""Search tools for Basic Memory MCP server."""

import logfire
from loguru import logger

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.search import SearchQuery, SearchResponse
from basic_memory.mcp.async_client import client


@logfire.instrument(extract_args=False)
@mcp.tool(
    description="Search across all content in basic-memory, including documents and entities",
)
async def search(query: SearchQuery, page: int = 1, page_size: int = 10) -> SearchResponse:
    """Search across all content in basic-memory.

    This tool searches the knowledge base using full-text search, pattern matching,
    or exact permalink lookup. It supports filtering by content type, entity type,
    and date.

    Args:
        query: SearchQuery object with search parameters including:
            - text: Full-text search (e.g., "project planning")
            - title: Search only in titles (e.g., "Meeting notes")
            - permalink: Exact permalink match (e.g., "docs/meeting-notes")
            - permalink_match: Pattern matching for permalinks (e.g., "docs/*-notes")
            - types: Optional list of content types to search (e.g., ["entity", "observation"])
            - entity_types: Optional list of entity types to filter by (e.g., ["note", "person"])
            - after_date: Optional date filter for recent content (e.g., "1 week", "2d")
        page: The page number of results to return (default 1)
        page_size: The number of results to return per page (default 10)

    Returns:
        SearchResponse with:
            - results: List of matching SearchResult objects with:
                - id: Internal ID
                - title: Document/entity title
                - type: Content type (entity, observation, relation)
                - score: Relevance score (higher = more relevant)
                - permalink: Permalink for accessing the content
                - file_path: File path on disk
                - metadata: Additional metadata about the result
            - current_page: Current page number
            - page_size: Number of results per page

    Examples:
        # Basic text search
        results = await search(SearchQuery(text="project planning"))

        # Search with type filter
        results = await search(SearchQuery(
            text="meeting notes",
            types=["entity"],
        ))

        # Search for recent content
        results = await search(SearchQuery(
            text="bug report",
            after_date="1 week"
        ))

        # Pattern matching on permalinks
        results = await search(SearchQuery(
            permalink_match="docs/meeting-*"
        ))
    """
    logger.info(f"Searching for {query}")
    response = await call_post(
        client,
        "/search/",
        json=query.model_dump(),
        params={"page": page, "page_size": page_size},
    )
    return SearchResponse.model_validate(response.json())
