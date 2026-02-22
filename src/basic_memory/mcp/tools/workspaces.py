"""Workspace discovery MCP tool."""

from typing import Literal

from fastmcp import Context

from basic_memory.mcp.project_context import get_available_workspaces
from basic_memory.mcp.server import mcp


@mcp.tool(
    description="List available cloud workspaces (tenant_id, type, role, and name).",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def list_workspaces(
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict:
    """List workspaces available to the current cloud user.

    Args:
        output_format: "text" returns human-readable workspace list.
            "json" returns structured workspace metadata.
        context: Optional FastMCP context for progress/status logging.
    """
    workspaces = await get_available_workspaces(context=context)

    if output_format == "json":
        return {
            "workspaces": [
                {
                    "tenant_id": ws.tenant_id,
                    "name": ws.name,
                    "workspace_type": ws.workspace_type,
                    "role": ws.role,
                    "organization_id": ws.organization_id,
                    "has_active_subscription": ws.has_active_subscription,
                }
                for ws in workspaces
            ],
            "count": len(workspaces),
        }

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
