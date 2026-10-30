"""Refuse a call that leaves out a required project, instead of failing validation."""

from typing import Any, override

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.tool_params import OutputFormat

DISCOVER_PROJECTS = "bm_project(command='list') to discover projects"


class MissingProjectMiddleware(Middleware):
    """Refuse a missing or blank ``project`` on any tool whose schema requires it.

    Without this, FastMCP's argument validation answers with pydantic's raw
    error (``missing_keyword_only_argument``, ``string_too_short``), which does
    not tell the model what ``project`` is or how to find one. A client holding a
    tool list cached from before ``project`` became required sends exactly that
    call (#1984).

    The check reads the tool's published input schema rather than a list of tool
    names, so it covers every tool registered on the server, including ones that
    replace or add to the core tools.
    """

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if context.fastmcp_context is None:
            return await call_next(context)
        name = context.message.name
        tool = await context.fastmcp_context.fastmcp.get_tool(name)
        # Trigger: an unknown tool, or one whose schema does not require project.
        # Why: FastMCP reports unknown tools itself, and optional project scope
        #      (bm_search, bm_project) is the tool's own decision.
        # Outcome: the call proceeds untouched.
        if tool is None or "project" not in _required(tool.parameters):
            return await call_next(context)

        arguments = context.message.arguments or {}
        if _given(arguments.get("project")) or _given(arguments.get("project_id")):
            return await call_next(context)

        raise refusal(
            "INVALID_ARGUMENTS",
            f"{name} needs `project`: the project name, 'workspace/project', or external id.",
            try_instead=DISCOVER_PROJECTS,
            output_format=_output_format(arguments, tool.parameters),
        )


def _required(schema: dict[str, Any]) -> list[Any]:
    required = schema.get("required")
    return required if isinstance(required, list) else []


def _given(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _output_format(arguments: dict[str, Any], schema: dict[str, Any]) -> OutputFormat:
    """The format the call asked for, else the tool's default, else text."""
    requested = arguments.get("output_format")
    if requested in ("text", "json"):
        return requested
    properties = schema.get("properties")
    output_format = properties.get("output_format") if isinstance(properties, dict) else None
    default = output_format.get("default") if isinstance(output_format, dict) else None
    return "json" if default == "json" else "text"
