"""Give empty list tool results a text block that text-only MCP clients can read."""

from typing import override

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent


class EmptyListResultMiddleware(Middleware):
    """Add text content to a tool result that is an empty list.

    FastMCP renders a list result as one JSON text block, but it renders an empty
    list as no content at all. The structured result still carries ``[]``, so a
    client that reads structured content sees the answer, while a client that
    reads only text content sees nothing and cannot tell "no results" from a
    failed call (#1634).
    """

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        # Trigger: the tool returned an empty list, so FastMCP emitted no content.
        # Why: text-only clients would otherwise receive an empty response.
        # Outcome: one "[]" block, the same JSON shape a non-empty list renders as,
        #          so clients that parse the joined text still get valid JSON. The
        #          structured result is unchanged.
        if result.content or result.structured_content != {"result": []}:
            return result
        return ToolResult(
            content=[TextContent(type="text", text="[]")],
            structured_content=result.structured_content,
            meta=result.meta,
        )
