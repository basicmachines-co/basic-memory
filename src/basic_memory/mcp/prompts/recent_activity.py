from typing import Annotated, Optional

from pydantic import Field

from basic_memory.mcp.prompts.utils import format_context_summary
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import recent_activity
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
    """Get recent activity from across the knowledge base."""
    results = await recent_activity(timeframe=timeframe)

    header = f"#Recent Activity: {timeframe}"
    return format_context_summary(header, results)
