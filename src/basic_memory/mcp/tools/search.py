"""Search tools for Basic Memory MCP server."""

from loguru import logger

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.search import SearchQuery, SearchResponse
from basic_memory.mcp.async_client import client


@mcp.tool(
    description="Search across all content in basic-memory, including documents and entities",
)
async def search(query: SearchQuery) -> SearchResponse:
    """Search across all content in basic-memory.

    Args:
        query: SearchQuery object with search parameters including:
            - text: Search text (required)
            - types: Optional list of content types to search ("document" or "entity")
            - entity_types: Optional list of entity types to filter by
            - after_date: Optional date filter for recent content

    Returns:
        SearchResponse with search results and metadata
    """
    logger.info(f"Searching for {query.text}")
    response = await call_post(client, "/search/", json=query.model_dump())
    return SearchResponse.model_validate(response.json())
