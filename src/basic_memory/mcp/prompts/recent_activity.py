"""Recent activity prompts for Basic Memory MCP server.

These prompts help users see what has changed in their knowledge base recently.
"""

from typing import Annotated, Optional

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.prompts.utils import format_context_summary
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.memory import recent_activity as recent_activity_tool
from basic_memory.schemas.base import TimeFrame


@mcp.prompt(
    name="recent_activity",
    description="Get recent activity from across the knowledge base",
)
async def recent_activity_prompt(
    timeframe: Annotated[
        Optional[TimeFrame],
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = None,
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

        results = await recent_activity_tool(timeframe=timeframe)

        time_display = f" ({timeframe})" if timeframe else ""
        header = f"# Recent Activity{time_display}"
        return format_context_summary(header, results)
