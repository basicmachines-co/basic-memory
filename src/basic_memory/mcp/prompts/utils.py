"""Utility functions for formatting prompt responses.

These utilities help format data from various tools into consistent,
user-friendly markdown summaries.
"""

from dataclasses import dataclass
from textwrap import dedent
from typing import List

from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    normalize_memory_url,
    EntitySummary,
    RelationSummary,
    ObservationSummary,
)


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
    if not context.results:  # pragma: no cover
        return dedent(f"""
            # Continuing conversation on: {context.topic}

            This is a memory retrieval session. 
            The supplied query did not return any information specifically on this topic.
            
            ## Opportunity to Capture New Knowledge!
            
            This is an excellent chance to start documenting this topic:
            
            ```python
            await write_note(
                title="{context.topic}",
                content=f'''
                # {context.topic}
                
                ## Overview
                [Summary of what we know about {context.topic}]
                
                ## Key Points
                [Main aspects or components of {context.topic}]
                
                ## Observations
                - [category] [First important observation about {context.topic}]
                - [category] [Second observation about {context.topic}]
                
                ## Relations
                - relates_to [[Related Topic]]
                - part_of [[Broader Context]]
                '''
            )
            ```
            
            ## Other Options
            
            Please use the available basic-memory tools to gather relevant context before responding.
            You can also:
            - Try a different search term
            - Check recent activity with `recent_activity(timeframe="1w")`
            """)

    # Start building our summary with header - add knowledge capture emphasis
    summary = dedent(f"""
        # Continuing conversation on: {context.topic}

        This is a memory retrieval session. 
        
        Please use the available basic-memory tools to gather relevant context before responding. 
        Start by executing one of the suggested commands below to retrieve content.

        Here's what I found from previous conversations:
        
        > **Knowledge Capture Recommendation:** As you continue this conversation, actively look for opportunities to record new information, decisions, or insights that emerge. Use `write_note()` to document important context.
        """)

    # Track what we've added to avoid duplicates
    added_permalinks = set()
    sections = []

    # Process each context
    for context in context.results:  # pyright: ignore
        for primary in context.primary_results:  # pyright: ignore
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

        if context.related_results:  # pyright: ignore
            section += dedent(  # pyright: ignore
                """   
                ## Related Context
                """
            )

            for related in context.related_results:  # pyright: ignore
                section_content = dedent(f"""
                    - type: **{related.type}**
                    - title: {related.title}
                    """)
                if related.permalink:  # pragma: no cover
                    section_content += (
                        f'You can view this document with: `read_note("{related.permalink}")`'
                    )
                else:  # pragma: no cover
                    section_content += (
                        f'You can view this file with: `read_file("{related.file_path}")`'
                    )

                section += section_content
                sections.append(section)

    # Add all sections
    summary += "\n".join(sections)
    return summary
