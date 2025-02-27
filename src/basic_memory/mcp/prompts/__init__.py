"""Basic Memory MCP prompts.

Prompts are a special type of tool that returns a string response
formatted for a user to read, typically invoking one or more tools
and transforming their results into user-friendly text.
"""

# Import individual prompt modules to register them with the MCP server
from basic_memory.mcp.prompts import guide
from basic_memory.mcp.prompts import session