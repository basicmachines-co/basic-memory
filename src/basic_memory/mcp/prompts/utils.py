"""Utility functions for formatting prompt responses.

These utilities help format data from various tools into consistent,
user-friendly markdown summaries.
"""
from dataclasses import dataclass
from textwrap import dedent
from typing import List

from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import GraphContext, normalize_memory_url, EntitySummary, RelationSummary, \
    ObservationSummary


@dataclass
class PromptContextItem:
    primary_results: List[EntitySummary]
    related_results: List[EntitySummary | RelationSummary | ObservationSummary] 
    
@dataclass
class PromptContext:
    timeframe: TimeFrame
    topic: str
    results: List[PromptContextItem]


def format_prompt_context(context: PromptContext) -> str:
    """Format continuation context into a helpful summary.
    Returns:
        Formatted continuation summary
    """
    if not context.results:
        return dedent(f"""
            # Continuing conversation on: {context.topic}

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
        # Continuing conversation on: {context.topic}

        This is a memory retrieval session. 
        
        Please use the available basic-memory tools to gather relevant context before responding. 
        Start by executing one of the suggested commands below to retrieve content.

        Here's what I found from previous conversations:
        """)

    # Track what we've added to avoid duplicates
    added_permalinks = set()
    sections = []

    # Process each context
    for context in context.results:
        for primary in context.primary_results:
            if primary.permalink not in added_permalinks:
                primary_permalink = primary.permalink
                
                added_permalinks.add(primary_permalink)
    
                memory_url = normalize_memory_url(primary_permalink)
                section = dedent(f"""
                    --- {memory_url}
                
                    ## {primary.title}
                    - **Type**: {primary.type}
                    """)
    
                # Add creation date
                section += f"- **Created**: {primary.created_at.strftime('%Y-%m-%d %H:%M')}\n"
    
                # Add content snippet
                if hasattr(primary, "content") and primary.content:  # pyright: ignore
                    content = primary.content or ""  # pyright: ignore
                    if content:
                        section += f"\n**Excerpt**:\n{content}\n"
    
                section += dedent(f"""
    
                    You can read this document with: `read_note("{primary_permalink}")`
                    """)
                sections.append(section)
                
        if context.related_results:
            section += dedent("""
                ## Related Context
                """)

            for related in context.related_results:   
                section_content = dedent(f"""
                    - type: **{related.type}**
                    - title: {related.title}
                    """)
                if related.permalink:
                    section_content += f'You can view this document with: `read_note("{related.permalink}")`'
                else:     
                    section_content += f'You can view this file with: `read_file("{related.file_path}")`'
                        

                section += section_content
                sections.append(section)
        

    # Add all sections
    summary += "\n".join(sections)
    return summary