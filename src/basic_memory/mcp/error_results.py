"""Give tool errors structured content that matches the tool's output schema."""

from typing import Any, override

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

# FastMCP marks an output schema that wraps a non-object return value as {"result": ...}.
WRAP_RESULT_KEY = "x-fastmcp-wrap-result"


class ToolErrorResultMiddleware(Middleware):
    """Return a tool error as an error result whose structured content fits the schema.

    FastMCP answers a ToolError with ``isError: true``, the message as text, and no
    structured content. That is valid MCP, but the bm_* tools advertise an output
    schema, and a client or gateway that validates structured content on every
    result rejects the missing field before the caller can read the message: the
    refusal it needed (which note already exists, what to call instead) is replaced
    by a schema error (#2094).

    The error keeps ``isError: true`` and the same text, and also carries the message
    as ``{"result": "<message>"}``, which every bm_* output schema accepts.
    """

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except ToolError as error:
            if context.fastmcp_context is None:
                raise
            tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
            # Trigger: an unknown tool, or one whose output schema can't hold the message.
            # Why: structured content that breaks the schema is worse than none.
            # Outcome: FastMCP reports the error as before.
            if tool is None or not wrapped_result_accepts_text(tool.output_schema):
                raise
            message = str(error)
            return ToolResult(
                content=[TextContent(type="text", text=message)],
                structured_content={"result": message},
                is_error=True,
            )


def wrapped_result_accepts_text(output_schema: dict[str, Any] | None) -> bool:
    """Whether a wrapped output schema's ``result`` accepts a string."""
    if not output_schema or not output_schema.get(WRAP_RESULT_KEY):
        return False
    result = output_schema.get("properties", {}).get("result", {})
    branches = result.get("anyOf", [result])
    return any(branch.get("type") == "string" for branch in branches)
