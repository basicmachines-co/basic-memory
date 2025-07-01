"""Sync status prompt for Basic Memory MCP server."""

from basic_memory.mcp.server import mcp


@mcp.prompt(
    description="""Get sync status with recommendations for AI assistants.
    
    This prompt provides both current sync status and guidance on how
    AI assistants should respond when sync operations are in progress or completed.
    """,
)
async def sync_status_prompt() -> str:
    """Get sync status with AI assistant guidance.
    Returns:
        Formatted sync status with AI assistant guidance
    """
    try:  # pragma: no cover
        from basic_memory.services.migration_service import migration_manager

        state = migration_manager.state

        # Build status report
        lines = [
            "# Basic Memory Sync Status",
            "",
            f"**Current Status**: {state.status.value.replace('_', ' ').title()}",
            f"**System Ready**: {'Yes' if migration_manager.is_ready else 'No'}",
            "",
        ]

        if migration_manager.is_ready:
            lines.extend(
                [
                    "✅ **All sync operations completed** - System is fully operational",
                    "",
                    "All Basic Memory tools are available and functioning normally.",
                    "File indexing is complete and knowledge graphs are up to date.",
                    "You can proceed with any knowledge management tasks.",
                ]
            )
        else:
            lines.append(f"**Status Message**: {state.message}")

            if state.status.value == "in_progress":
                if state.projects_total > 0:
                    progress = f" ({state.projects_migrated}/{state.projects_total})"
                    lines.append(f"**Progress**: {progress}")

                lines.extend(
                    [
                        "",
                        "🔄 **File synchronization in progress** - Processing files and building knowledge graphs",
                        "",
                        "**Impact**: Some tools may show status messages instead of normal responses",
                        "until sync completes (usually 1-3 minutes).",
                    ]
                )

            elif state.status.value == "failed":
                lines.extend(
                    [
                        "",
                        f"❌ **Sync failed**: {state.error or 'Unknown error'}",
                        "",
                        "**Impact**: System may have limited functionality until issue is resolved.",
                    ]
                )

        # Add AI assistant recommendations
        if not migration_manager.is_ready:
            lines.extend(
                [
                    "",
                    "---",
                    "",
                    "## AI Assistant Recommendations",
                    "",
                    "**When sync is in progress:**",
                    "- Inform the user about the background file processing",
                    "- Suggest using `sync_status()` tool to check progress",
                    "- Explain that tools will work normally once sync completes",
                    "- Avoid creating complex workflows until sync is done",
                    "",
                    "**What to tell users:**",
                    "- 'Basic Memory is processing your files and building knowledge graphs'",
                    "- 'This usually takes 1-3 minutes depending on your content size'",
                    "- 'You can check progress anytime with the sync_status tool'",
                    "- 'Full functionality will be available once processing completes'",
                    "",
                    "**User-friendly language:**",
                    "- Say 'processing files' instead of 'migration' or 'sync'",
                    "- Say 'building knowledge graphs' instead of 'indexing'",
                    "- Say 'setting up your knowledge base' instead of 'running migrations'",
                ]
            )

        return "\n".join(lines)

    except Exception as e:  # pragma: no cover
        return f"""# Sync Status - Error

❌ **Unable to check sync status**: {str(e)}

## AI Assistant Recommendations

**When status is unavailable:**
- Assume the system is likely working normally
- Try proceeding with normal operations
- If users report issues, suggest checking logs or restarting
- Use user-friendly language about 'setting up the knowledge base'
"""
