"""Workspace discovery MCP tool."""

from fastmcp import Context

from basic_memory.mcp.project_context import get_available_workspaces
from basic_memory.mcp.server import mcp


@mcp.tool(description="List available cloud workspaces (tenant_id, type, role, and name).")
async def list_workspaces(context: Context | None = None) -> str:
    """List workspaces available to the current cloud user."""
    workspaces = await get_available_workspaces(context=context)

    if not workspaces:
        return (
            "# No Workspaces Available\n\n"
            "No accessible workspaces were found for this account. "
            "Ensure the account has an active subscription and tenant access."
        )

    lines = [
        f"# Available Workspaces ({len(workspaces)})",
        "",
        "Use `workspace` as either the `tenant_id` or unique `name` in project-scoped tool calls.",
        "",
    ]
    for workspace in workspaces:
        lines.append(
            f"- {workspace.name} "
            f"(type={workspace.workspace_type}, role={workspace.role}, tenant_id={workspace.tenant_id})"
        )

    return "\n".join(lines)
