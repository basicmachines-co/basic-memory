"""Utility functions for formatting prompt responses.

These utilities help format data from various tools into consistent,
user-friendly markdown summaries.
"""

from textwrap import dedent
from typing import List

from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import GraphContext, normalize_memory_url


def format_prompt_context(
    topic: str, contexts: List[GraphContext], timeframe: TimeFrame | None
) -> str:
    """Format continuation context into a helpful summary.

    Args:
        topic: The topic or focus of continuation
        contexts: List of context graphs
        timeframe: How far back to look for activity

    Returns:
        Formatted continuation summary
    """
    if not contexts or all(not context.primary_results for context in contexts):
        return dedent(f"""
            # Continuing conversation on: {topic}

            This is a memory retrieval session. 
            The supplied query did not return any information specifically on this topic.
            
            Please use the available basic-memory tools to gather relevant context before responding. 
            Start by executing one of the suggested commands below to retrieve content.

            ## Suggestions
            - Try a different search term
            - Check recent activity with `recent_activity(timeframe="1w")`
            - Start a new topic with `write_note(...)`
            """)

    # Start building our summary with header
    summary = dedent(f"""
        # Continuing conversation on: {topic}

        This is a memory retrieval session. 
        
        Please use the available basic-memory tools to gather relevant context before responding. 
        Start by executing one of the suggested commands below to retrieve content.

        Here's what I found about the previous conversation:
        """)

    # Track what we've added to avoid duplicates
    added_permalinks = set()
    sections = []

    # Process each context
    for context in contexts:
        # Add primary results
        for primary in context.primary_results:
            if hasattr(primary, "permalink") and primary.permalink not in added_permalinks:
                added_permalinks.add(primary.permalink)

                memory_url = normalize_memory_url(primary.permalink)
                section = dedent(f"""
                    --- {memory_url}
                
                    ## {primary.title}
                    - **Type**: {primary.type}
                    """)

                # Add creation date if available
                if hasattr(primary, "created_at"):
                    section += f"- **Created**: {primary.created_at.strftime('%Y-%m-%d %H:%M')}\n"

                # Add content snippet
                if hasattr(primary, "content") and primary.content:  # pyright: ignore
                    content = primary.content or ""  # pyright: ignore
                    if content:
                        section += f"- **Content Snippet**: {content}\n"

                section += dedent(f"""

                    You can read this document with: `read_note("{primary.permalink}")`
                    """)

        
            # Add related documents if available
            # Group by relation type for better organization
            relation_types = {}
            for rel in context.related_results:
                if hasattr(rel, "relation_type"):
                    rel_type = rel.relation_type  # pyright: ignore
                    if rel_type not in relation_types:
                        relation_types[rel_type] = []
                    relation_types[rel_type].append(rel)
    
            if relation_types:
                section += dedent("""
                    ## Related Context
                    """)
                for rel_type, relations in relation_types.items():
                    display_type = rel_type.replace("_", " ").title()
                    section += f"- **{display_type}**:\n"
                    for rel in relations[:3]:  # Limit to avoid overwhelming
                        if hasattr(rel, "to_entity") and rel.to_entity:
                            section += f"  - `{rel.to_entity}`\n"

            sections.append(section)

    # Add all sections
    summary += "\n".join(sections)
    return summary