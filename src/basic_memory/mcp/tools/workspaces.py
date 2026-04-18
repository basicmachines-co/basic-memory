"""Workspace discovery MCP tool."""

from typing import Literal

from fastmcp import Context

from basic_memory.mcp.project_context import get_available_workspaces
from basic_memory.mcp.server import mcp
from basic_memory.schemas.cloud import WorkspaceInfo, WorkspaceListResponse


def _personal_workspace() -> WorkspaceInfo:
    """Return a display-only personal workspace when discovery has no rows.

    This keeps list_workspaces friendly for non-teams or local-only users. It is not
    the cloud routing source of truth; project-scoped routing still depends on real
    workspace discovery and the workspace project index in project_context.
    """
    return WorkspaceInfo(
        tenant_id="personal",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
        has_active_subscription=True,
    )


def _workspace_list_response(workspaces: list[WorkspaceInfo]) -> WorkspaceListResponse:
    """Build the structured MCP response from the shared cloud workspace schema."""
    if not workspaces:
        workspaces = [_personal_workspace()]

    default_workspace_id = next(
        (workspace.tenant_id for workspace in workspaces if workspace.is_default),
        None,
    )
    return WorkspaceListResponse(
        workspaces=workspaces,
        count=len(workspaces),
        default_workspace_id=default_workspace_id,
        current_workspace_id=None,
    )


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
    response = _workspace_list_response(workspaces)

    if output_format == "json":
        return response.model_dump(mode="json")

    lines = [f"# Available Workspaces ({response.count})", ""]
    for workspace in response.workspaces:
        default_label = ", default" if workspace.is_default else ""
        lines.append(
            f"- {workspace.name} "
            f"(slug={workspace.slug}, type={workspace.workspace_type}, "
            f"role={workspace.role}{default_label}, tenant_id={workspace.tenant_id})"
        )

    return "\n".join(lines)
