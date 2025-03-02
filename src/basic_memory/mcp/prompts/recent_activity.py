"""Recent activity prompts for Basic Memory MCP server.

These prompts help users see what has changed in their knowledge base recently.
"""

from typing import Annotated, Optional

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.prompts.utils import format_prompt_context
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.recent_activity import recent_activity as recent_activity_tool
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.search import SearchItemType


@mcp.prompt(
    name="Share Recent Activity",
    description="Get recent activity from across the knowledge base",
)
async def recent_activity_prompt(
    timeframe: Annotated[
        TimeFrame,
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = "7d",
) -> str:
    """Get recent activity from across the knowledge base.

    This prompt helps you see what's changed recently in the knowledge base,
    showing new or updated documents and related information.

    Args:
        timeframe: How far back to look for activity (e.g. '1d', '1 week')

    Returns:
        Formatted summary of recent activity
    """
    with logfire.span("Getting recent activity", timeframe=timeframe):  # pyright: ignore
        logger.info(f"Getting recent activity, timeframe: {timeframe}")

        results = await recent_activity_tool(timeframe=timeframe, type=[SearchItemType.ENTITY])

        header = f"Recent Activity from ({timeframe})"
        return format_prompt_context(header, [results], timeframe)
