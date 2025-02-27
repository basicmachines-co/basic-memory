"""Session continuation prompts for Basic Memory MCP server.

These prompts help users continue conversations and work across sessions.
"""

from typing import Optional, List, Annotated

from loguru import logger
import logfire
from pydantic import Field

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.memory import build_context, recent_activity
from basic_memory.mcp.tools.search import search
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import GraphContext


@mcp.prompt(
    name="continue_session",
    description="Continue a previous conversation or work session",
)
async def continue_session(
    topic: Annotated[
        Optional[str], Field(description="Topic or keyword to search for")
    ] = None,
    timeframe: Annotated[
        TimeFrame, Field(description="How far back to look for activity (e.g. '1d', '1 week'")
    ] = "1w",
) -> str:
    """Continue a previous conversation or work session.
    
    This prompt helps you pick up where you left off by finding recent context
    about a specific topic or showing general recent activity.
    
    Args:
        topic: Topic or keyword to search for (optional)
        timeframe: How far back to look for activity
        
    Returns:
        Context from previous sessions on this topic
    """
    with logfire.span("Continuing session", topic=topic, timeframe=timeframe):  # pyright: ignore
        logger.info(f"Continuing session, topic: {topic}, timeframe: {timeframe}")
        
        # If topic provided, search for it
        if topic:
            search_results = await search({"text": topic, "timeframe": timeframe})
            
            # Build context from top results
            contexts = []
            for result in search_results.primary_results[:3]:
                if hasattr(result, "permalink") and result.permalink:
                    try:
                        context = await build_context(f"memory://{result.permalink}")
                        contexts.append(context)
                    except Exception as e:
                        logger.warning(f"Error building context for {result.permalink}: {e}")
                    
            return _format_continuation_context(topic, contexts, search_results)
        
        # If no topic, get recent activity
        recent = await recent_activity(timeframe=timeframe)
        return _format_continuation_context("Recent Activity", [recent], None)


def _format_continuation_context(
    topic: str, 
    contexts: List[GraphContext], 
    search_results: Optional[object]
) -> str:
    """Format continuation context into a helpful summary.
    
    Args:
        topic: The topic or focus of continuation
        contexts: List of context graphs
        search_results: Original search results if from search
        
    Returns:
        Formatted continuation summary
    """
    summary = [f"# Continuing Work on: {topic}", ""]
    
    if not contexts or all(not context.primary_results for context in contexts):
        summary.append("I couldn't find any recent work specifically on this topic.")
        summary.append("")
        summary.append("## Suggestions")
        summary.append("- Try a different search term")
        summary.append("- Check recent activity with `recent_activity(timeframe=\"1w\")`")
        summary.append("- Start a new topic with `write_note(...)`")
        return "\n".join(summary)
    
    # Add overview
    summary.append("Here's what I found about your previous work:")
    summary.append("")
    
    # Track what we've added to avoid duplicates
    added_permalinks = set()
    
    # Process each context
    for context in contexts:
        # Add primary results
        for primary in context.primary_results:
            if hasattr(primary, "permalink") and primary.permalink not in added_permalinks:
                added_permalinks.add(primary.permalink)
                
                summary.append(f"## {primary.title}")
                summary.append(f"- **Type**: {primary.type}")
                
                # Add creation date if available
                if hasattr(primary, "created_at"):
                    summary.append(f"- **Created**: {primary.created_at.strftime('%Y-%m-%d %H:%M')}")
                
                summary.append("")
                summary.append(f"You can read this document with: `read_note(\"{primary.permalink}\")`")
                summary.append("")
                
                # Add related documents if available
                related_by_type = {}
                if context.related_results:
                    for related in context.related_results:
                        if hasattr(related, "relation_type") and related.relation_type:
                            if related.relation_type not in related_by_type:
                                related_by_type[related.relation_type] = []
                            related_by_type[related.relation_type].append(related)
                    
                    if related_by_type:
                        summary.append("### Related Documents")
                        for rel_type, relations in related_by_type.items():
                            display_type = rel_type.replace("_", " ").title()
                            summary.append(f"- **{display_type}**:")
                            for rel in relations[:3]:  # Limit to avoid overwhelming
                                if hasattr(rel, "to_id") and rel.to_id:
                                    summary.append(f"  - `{rel.to_id}`")
                        summary.append("")
    
    # Add next steps
    summary.append("## Next Steps")
    summary.append("")
    summary.append("You can:")
    summary.append(f"- Explore more with: `search({{\"text\": \"{topic}\"}})`")
    summary.append(f"- See what's changed: `recent_activity(timeframe=\"{timeframe}\")`")
    
    # Add specific exploration based on what we found
    if added_permalinks:
        first_permalink = next(iter(added_permalinks))
        summary.append(f"- Continue the discussion: `build_context(\"memory://{first_permalink}\")`")
    
    return "\n".join(summary)