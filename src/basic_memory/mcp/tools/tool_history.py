"""Tool history MCP tool for Basic Memory."""

import json
from typing import Optional

from fastmcp import Context

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_history import ToolHistoryTracker, get_tracker


@mcp.tool(
    description="Get the history of previous MCP tool calls. Useful for debugging, auditing workflows, and referencing previous operations."
)
async def tool_history(
    limit: int = 10,
    tool_name: Optional[str] = None,
    include_inputs: bool = True,
    include_outputs: bool = False,
    since: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Get the history of previous MCP tool calls.

    Args:
        limit: Maximum number of tool calls to return (default: 10)
        tool_name: Filter by specific tool name (e.g., "write_note", "search")
        include_inputs: Include input parameters in response (default: True)
        include_outputs: Include output/results in response (default: False)
        since: Filter calls since a specific time (e.g., "1h ago", "30m ago", "2024-01-20")
        context: FastMCP context (automatically provided)

    Returns:
        A formatted string containing tool call history information.

    Examples:
        - Get last 5 tool calls: tool_history(limit=5)
        - Get all write_note calls from last hour: tool_history(tool_name="write_note", since="1h ago")
        - Get recent searches with outputs: tool_history(tool_name="search", include_outputs=True)
    """
    tracker = get_tracker()

    # Get filtered history
    calls = await tracker.get_history(
        limit=limit,
        tool_name=tool_name,
        include_inputs=include_inputs,
        include_outputs=include_outputs,
        since=since,
    )

    if not calls:
        return "No tool calls found matching the specified criteria."

    # Format the response
    result_lines = [f"## Tool Call History ({len(calls)} calls)\n"]

    if since:
        result_lines.append(f"**Filter:** Since {since}\n")
    if tool_name:
        result_lines.append(f"**Tool:** {tool_name}\n")

    result_lines.append("")  # Empty line for spacing

    for call in calls:
        # Format timestamp
        call_dict = call.to_dict()
        timestamp = call_dict["timestamp"]

        result_lines.append(f"### {call.tool_name} - {call.id}")
        result_lines.append(f"**Time:** {timestamp}")
        result_lines.append(f"**Status:** {call.status}")

        if call.execution_time_ms is not None:
            result_lines.append(f"**Duration:** {call.execution_time_ms:.2f}ms")

        if include_inputs and call.input:
            result_lines.append("\n**Input Parameters:**")
            result_lines.append("```json")
            result_lines.append(json.dumps(call.input, indent=2))
            result_lines.append("```")

        if include_outputs and call.output:
            result_lines.append("\n**Output:**")
            if isinstance(call.output, str):
                # For string outputs, show as-is (truncated if needed)
                output_str = call.output[:1000] if len(call.output) > 1000 else call.output
                if len(call.output) > 1000:
                    output_str += "\n... (truncated)"
                result_lines.append(f"```\n{output_str}\n```")
            else:
                # For other types, JSON format
                result_lines.append("```json")
                result_lines.append(json.dumps(call.output, indent=2))
                result_lines.append("```")

        if call.error:
            result_lines.append(f"\n**Error:** {call.error}")

        result_lines.append("")  # Empty line between calls

    # Add summary statistics
    total_count = len(calls)
    success_count = len([c for c in calls if c.status == "success"])
    error_count = len([c for c in calls if c.status == "error"])

    result_lines.append("---")
    result_lines.append(f"**Summary:** {total_count} calls ({success_count} successful, {error_count} errors)")

    return "\n".join(result_lines)


@mcp.tool(
    description="Get detailed information about a specific tool call by its ID."
)
async def get_tool_call(
    call_id: str,
    context: Context | None = None,
) -> str:
    """Get detailed information about a specific tool call.

    Args:
        call_id: The ID of the tool call to retrieve (e.g., "call_000001_1234567890")
        context: FastMCP context (automatically provided)

    Returns:
        Detailed information about the specified tool call, or error message if not found.
    """
    tracker = get_tracker()
    call = await tracker.get_call_by_id(call_id)

    if not call:
        return f"Tool call with ID '{call_id}' not found in history."

    # Format detailed response
    call_dict = call.to_dict()
    timestamp = call_dict["timestamp"]

    result_lines = [
        f"## Tool Call Details: {call.id}\n",
        f"**Tool:** {call.tool_name}",
        f"**Time:** {timestamp}",
        f"**Status:** {call.status}",
    ]

    if call.execution_time_ms is not None:
        result_lines.append(f"**Duration:** {call.execution_time_ms:.2f}ms")

    if call.input:
        result_lines.append("\n### Input Parameters")
        result_lines.append("```json")
        result_lines.append(json.dumps(call.input, indent=2))
        result_lines.append("```")

    if call.output:
        result_lines.append("\n### Output")
        if isinstance(call.output, str):
            result_lines.append(f"```\n{call.output}\n```")
        else:
            result_lines.append("```json")
            result_lines.append(json.dumps(call.output, indent=2))
            result_lines.append("```")

    if call.error:
        result_lines.append(f"\n### Error\n```\n{call.error}\n```")

    return "\n".join(result_lines)


@mcp.tool(
    description="Clear all tool call history. Use with caution as this cannot be undone."
)
async def clear_tool_history(
    context: Context | None = None,
) -> str:
    """Clear all tool call history.

    Args:
        context: FastMCP context (automatically provided)

    Returns:
        Confirmation message.
    """
    tracker = get_tracker()
    await tracker.clear_history()
    return "Tool call history has been cleared."