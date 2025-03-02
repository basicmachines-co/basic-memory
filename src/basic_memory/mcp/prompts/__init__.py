"""Basic Memory MCP prompts.

Prompts are a special type of tool that returns a string response
formatted for a user to read, typically invoking one or more tools
and transforming their results into user-friendly text.
"""

# Import individual prompt modules to register them with the MCP server
from basic_memory.mcp.prompts import continue_conversation
from basic_memory.mcp.prompts import recent_activity
from basic_memory.mcp.prompts import search

__all__ = [
    "continue_conversation",
    "recent_activity",
    "search",
]
