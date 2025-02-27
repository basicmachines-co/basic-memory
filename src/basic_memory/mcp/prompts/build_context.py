from typing import Annotated

from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import build_context, recent_activity
from basic_memory.schemas.memory import (
    GraphContext,
    MemoryUrl,
)


@mcp.prompt(
    name="build_context",
    description="Build context from a memory:// URI",
)
async def build_context(url: MemoryUrl) -> str:
    """Build context from a memory:// URI."""
    context = await build_context(url=url)
    return format_context_summary(f"# Knowledge Context: {url}", context)


@mcp.prompt(
    name="recent_activity",
    description="Get recent activity from across the knowledge base",
)
async def recent_activity(
    instructions: Annotated[
        str, Field(description="How to process or focus on the recent activity")
    ] = None,
) -> str:
    """Get recent activity from across the knowledge base."""
    timeframe = "2d"
    results = await recent_activity(timeframe=timeframe)

    header = f"{instructions}\n #Recent Activity: {timeframe}"
    return format_context_summary(header, results)


def format_context_summary(header: str, context: GraphContext) -> str:
    """Format GraphContext as a helpful markdown summary.

    This creates a user-friendly markdown response that explains the context
    and provides guidance on how to explore further.
    """
    summary = []

    # Extract URI for reference
    uri = context.metadata.uri or "a/permalink-value"

    # Add header
    summary.append(f"{header}")
    summary.append("")

    # Primary document section
    if context.primary_results:
        summary.append(f"## Primary Documents ({len(context.primary_results)})")

        for primary in context.primary_results:
            summary.append(f"### {primary.title}")
            summary.append(f"- **Type**: {primary.type}")
            summary.append(f"- **Path**: {primary.file_path}")
            summary.append(f"- **Created**: {primary.created_at.strftime('%Y-%m-%d %H:%M')}")
            summary.append("")
            summary.append(
                f'To view this document\'s content: `read_note("{primary.permalink}")` or `read_note("{primary.title}")` '
            )
            summary.append("")
    else:
        summary.append("\nNo primary documents found.")

    # Related documents section
    if context.related_results:
        summary.append(f"## Related Documents ({len(context.related_results)})")

        # Group by relation type for better organization
        relation_types = {}
        for rel in context.related_results:
            if hasattr(rel, "relation_type"):
                rel_type = rel.relation_type
                if rel_type not in relation_types:
                    relation_types[rel_type] = []
                relation_types[rel_type].append(rel)

        # Display relations grouped by type
        for rel_type, relations in relation_types.items():
            summary.append(f"### {rel_type.replace('_', ' ').title()} ({len(relations)})")

            for rel in relations:
                if hasattr(rel, "to_id") and rel.to_id:
                    summary.append(f"- **{rel.to_id}**")
                    summary.append(f'  - View document: `read_note("{rel.to_id}")` ')
                    summary.append(
                        f'  - Explore connections: `build_context("memory://{rel.to_id}")` '
                    )
                else:
                    summary.append(f"- **Unresolved relation**: {rel.permalink}")
            summary.append("")

    # Next steps section
    summary.append("## Next Steps")
    summary.append("Here are some ways to explore further:")

    search_term = uri.split("/")[-1]
    summary.append(f'- **Search related topics**: `search({{"text": "{search_term}"}})`')

    summary.append('- **Check recent changes**: `recent_activity(timeframe="3 days")`')
    summary.append(f'- **Explore all relations**: `build_context("memory://{uri}/*")`')

    # Tips section
    summary.append("")
    summary.append("## Tips")
    summary.append(
        f'- For more specific context, increase depth: `build_context("memory://{uri}", depth=2)`'
    )
    summary.append(
        "- You can follow specific relation types using patterns like: `memory://document/relation-type/*`"
    )
    summary.append("- Look for connected documents by checking relations between them")

    return "\n".join(summary)
