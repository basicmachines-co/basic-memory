"""Sync status tool for Basic Memory MCP server."""

from typing import Optional

from loguru import logger
from fastmcp import Context

from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.project_context import get_active_project
from basic_memory.services.sync_status_service import sync_status_tracker


def _get_all_projects_status() -> list[str]:
    """Get status lines for all configured projects."""
    status_lines = []

    try:
        app_config = ConfigManager().config

        if app_config.projects:
            status_lines.extend(["", "---", "", "**All Projects Status:**"])

            for project_name, project_path in app_config.projects.items():
                # Check if this project has sync status
                project_sync_status = sync_status_tracker.get_project_status(project_name)

                if project_sync_status:
                    # Project has tracked sync activity
                    if project_sync_status.status.value == "watching":
                        # Project is actively watching for changes (steady state)
                        status_icon = "👁️"
                        status_text = "Watching for changes"
                    elif project_sync_status.status.value == "completed":
                        # Sync completed but not yet watching - transitional state
                        status_icon = "✅"
                        status_text = "Sync completed"
                    elif project_sync_status.status.value in ["scanning", "syncing"]:
                        status_icon = "🔄"
                        status_text = "Sync in progress"
                        if project_sync_status.files_total > 0:
                            progress_pct = (
                                project_sync_status.files_processed
                                / project_sync_status.files_total
                            ) * 100
                            status_text += f" ({project_sync_status.files_processed}/{project_sync_status.files_total}, {progress_pct:.0f}%)"
                    elif project_sync_status.status.value == "failed":
                        status_icon = "❌"
                        status_text = f"Sync error: {project_sync_status.error or 'Unknown error'}"
                    else:
                        status_icon = "⏸️"
                        status_text = project_sync_status.status.value.title()
                else:
                    # Project has no tracked sync activity - will be synced automatically
                    status_icon = "⏳"
                    status_text = "Pending sync"

                status_lines.append(f"- {status_icon} **{project_name}**: {status_text}")

    except Exception as e:
        logger.debug(f"Could not get project config for comprehensive status: {e}")

    return status_lines


@mcp.tool(
    description="""Monitors file synchronization and background operations. Essential for understanding system state during indexing, migrations, and sync operations.

```yaml
node:
  topic: sync_status - Operation Monitor
  goal: Track system synchronization state
  insight: Real-time visibility into background ops
  context:
    states: [idle, indexing, migrating, syncing, error]
    monitoring: Progress tracking with estimates
    use_case: Pre-operation checks, debugging
```

```baml
class SyncStatusInput {
  project string?
  verbose boolean @default(false)
}

class SyncStatusOutput {
  state ("idle" | "indexing" | "migrating" | "syncing" | "error")
  progress float? @range(0, 100)
  message string
  files_processed int?
  files_total int?
  errors string[]?
  estimated_time string? @format("duration")
}

function sync_status(SyncStatusInput) -> SyncStatusOutput {
  @description("Monitor synchronization and background operations")
  @real_time(true)
  @async(true)
}
```

## Monitoring Patterns
```python
# Basic check
status = sync_status()
if status["state"] == "idle":
    print("System ready")

# Detailed monitoring
status = sync_status(verbose=True)
if status["state"] == "indexing":
    print(f"Progress: {status['progress']}%")
    print(f"Files: {status['files_processed']}/{status['files_total']}")
```

Performance: Check 10-30ms | Non-blocking | Real-time updates""",
)
async def sync_status(project: Optional[str] = None, context: Context | None = None) -> str:
    """Get current sync status and system readiness information.

    This tool provides detailed information about any ongoing or completed
    sync operations, helping users understand when their files are ready.

    Args:
        project: Optional project name to get project-specific context

    Returns:
        Formatted sync status with progress, readiness, and guidance
    """
    logger.info("MCP tool call tool=sync_status")

    async with get_client() as client:
        status_lines = []

        try:
            from basic_memory.services.sync_status_service import sync_status_tracker

            # Get overall summary
            summary = sync_status_tracker.get_summary()
            is_ready = sync_status_tracker.is_ready

            # Header
            status_lines.extend(
                [
                    "# Basic Memory Sync Status",
                    "",
                    f"**Current Status**: {summary}",
                    f"**System Ready**: {'✅ Yes' if is_ready else '🔄 Processing'}",
                    "",
                ]
            )

            if is_ready:
                status_lines.extend(
                    [
                        "✅ **All sync operations completed**",
                        "",
                        "- File indexing is complete",
                        "- Knowledge graphs are up to date",
                        "- All Basic Memory tools are fully operational",
                        "",
                        "Your knowledge base is ready for use!",
                    ]
                )

                # Show all projects status even when ready
                status_lines.extend(_get_all_projects_status())
            else:
                # System is still processing - show both active and all projects
                all_sync_projects = sync_status_tracker.get_all_projects()

                active_projects = [
                    p
                    for p in all_sync_projects.values()
                    if p.status.value in ["scanning", "syncing"]
                ]
                failed_projects = [
                    p for p in all_sync_projects.values() if p.status.value == "failed"
                ]

                if active_projects:
                    status_lines.extend(
                        [
                            "🔄 **File synchronization in progress**",
                            "",
                            "Basic Memory is automatically processing all configured projects and building knowledge graphs.",
                            "This typically takes 1-3 minutes depending on the amount of content.",
                            "",
                            "**Currently Processing:**",
                        ]
                    )

                    for project_status in active_projects:
                        progress = ""
                        if project_status.files_total > 0:
                            progress_pct = (
                                project_status.files_processed / project_status.files_total
                            ) * 100
                            progress = f" ({project_status.files_processed}/{project_status.files_total}, {progress_pct:.0f}%)"

                        status_lines.append(
                            f"- **{project_status.project_name}**: {project_status.message}{progress}"
                        )

                    status_lines.extend(
                        [
                            "",
                            "**What's happening:**",
                            "- Scanning and indexing markdown files",
                            "- Building entity and relationship graphs",
                            "- Settings up full-text search indexes",
                            "- Processing file changes and updates",
                            "",
                            "**What you can do:**",
                            "- Wait for automatic processing to complete - no action needed",
                            "- Use this tool again to check progress",
                            "- Simple operations may work already",
                            "- All projects will be available once sync finishes",
                        ]
                    )

                # Handle failed projects (independent of active projects)
                if failed_projects:
                    status_lines.extend(["", "❌ **Some projects failed to sync:**", ""])

                    for project_status in failed_projects:
                        status_lines.append(
                            f"- **{project_status.project_name}**: {project_status.error or 'Unknown error'}"
                        )

                    status_lines.extend(
                        [
                            "",
                            "**Next steps:**",
                            "1. Check the logs for detailed error information",
                            "2. Ensure file permissions allow read/write access",
                            "3. Try restarting the MCP server",
                            "4. If issues persist, consider filing a support issue",
                        ]
                    )
                elif not active_projects:
                    # No active or failed projects - must be pending
                    status_lines.extend(
                        [
                            "⏳ **Sync operations pending**",
                            "",
                            "File synchronization has been queued but hasn't started yet.",
                            "This usually resolves automatically within a few seconds.",
                        ]
                    )

            # Add comprehensive project status for all configured projects
            all_projects_status = _get_all_projects_status()
            if all_projects_status:
                status_lines.extend(all_projects_status)

                # Add explanation about automatic syncing if there are unsynced projects
                unsynced_count = sum(1 for line in all_projects_status if "⏳" in line)
                if unsynced_count > 0 and not is_ready:
                    status_lines.extend(
                        [
                            "",
                            "**Note**: All configured projects will be automatically synced during startup.",
                        ]
                    )

            # Add project context if provided
            if project:
                try:
                    active_project = await get_active_project(client, project, context)
                    status_lines.extend(
                        [
                            "",
                            "---",
                            "",
                            f"**Active Project**: {active_project.name}",
                            f"**Project Path**: {active_project.home}",
                        ]
                    )
                except Exception as e:
                    logger.debug(f"Could not get project info: {e}")

            return "\n".join(status_lines)

        except Exception as e:
            return f"""# Sync Status - Error

❌ **Unable to check sync status**: {str(e)}

**Troubleshooting:**
- The system may still be starting up
- Try waiting a few seconds and checking again
- Check logs for detailed error information
- Consider restarting if the issue persists
"""
