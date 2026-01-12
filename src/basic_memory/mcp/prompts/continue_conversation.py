"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions,
providing context from previous interactions to maintain continuity.
"""

from typing import Annotated, Optional

from loguru import logger
from pydantic import Field

from basic_memory.config import get_project_config
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.prompt import ContinueConversationRequest


@mcp.prompt(
    name="continue_conversation",
    description="Continue a previous conversation",
)
async def continue_conversation(
    topic: Annotated[Optional[str], Field(description="Topic or keyword to search for")] = None,
    timeframe: Annotated[
        Optional[TimeFrame],
        Field(description="How far back to look for activity (e.g. '1d', '1 week')"),
    ] = None,
) -> str:
    """Continue a previous conversation or work session.

    This prompt helps you pick up where you left off by finding recent context
    about a specific topic or showing general recent activity.

    Args:
        topic: Topic or keyword to search for (optional)
        timeframe: How far back to look for activity

    Returns:
        Context from previous sessions on this topic
    """
    logger.info(f"Continuing session, topic: {topic}, timeframe: {timeframe}")

    async with get_client() as client:
        # Create request model
        request = ContinueConversationRequest(  # pyright: ignore [reportCallIssue]
            topic=topic, timeframe=timeframe
        )

        project_url = get_project_config().project_url

        # Call the prompt API endpoint
        response = await call_post(
            client,
            f"{project_url}/prompt/continue-conversation",
            json=request.model_dump(exclude_none=True),
        )

        # Extract the rendered prompt from the response
        result = response.json()
        return result["prompt"]
