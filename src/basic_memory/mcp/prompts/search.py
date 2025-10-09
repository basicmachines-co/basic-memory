"""Search prompts for Basic Memory MCP server.

These prompts help users search and explore their knowledge base.
"""

from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.config import get_project_config
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.prompt import SearchPromptRequest


@mcp.prompt(
    name="search_knowledge_base",
    description="Search across all content in basic-memory",
)
async def search_prompt(
    query: str,
    timeframe: Annotated[
        Optional[TimeFrame],
        Field(description="How far back to search (e.g. '1d', '1 week')"),
    ] = None,
) -> str:
    """Search across all content in basic-memory.

    This prompt helps search for content in the knowledge base and
    provides helpful context about the results.

    Args:
        query: The search text to look for
        timeframe: Optional timeframe to limit results (e.g. '1d', '1 week')

    Returns:
        Formatted search results with context
    """
    logger.info(f"Searching knowledge base, query: {query}, timeframe: {timeframe}")

    async with get_client() as client:
        # Create request model
        request = SearchPromptRequest(query=query, timeframe=timeframe)

        project_url = get_project_config().project_url

        # Call the prompt API endpoint
        response = await call_post(
            client, f"{project_url}/prompt/search", json=request.model_dump(exclude_none=True)
        )

        # Extract the rendered prompt from the response
        result = response.json()
        return result["prompt"]
