"""Prompt text for the v2 prompt routes.

The routes used to render Handlebars templates through pybars3, which is unmaintained
and carries CVE-2026-119716 (#2044). Both prompts are fixed text around a list of
results, so plain Python builds them: no template source is ever compiled, and the
user's query and note text are only ever inserted as values.
"""

import datetime
from collections.abc import Sequence
from typing import Any

from basic_memory.schemas.memory import ContextResult, EntitySummary, ObservationSummary
from basic_memory.schemas.search import SearchResult

# The search prompt lists at most this many results in full; the rest stay in the
# response's structured context.
SEARCH_PROMPT_RESULT_LIMIT = 5


def format_timestamp(value: Any) -> str:
    """Render a datetime or ISO string as `YYYY-MM-DD HH:MM`; pass anything else through."""
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value
    return str(value)


def read_hint(permalink: str | None, file_path: str, *, verb: str) -> str:
    """Tell the agent how to open a result: by permalink for notes, by path for files."""
    if permalink:
        return f'You can {verb} this document with: `bm_read("{permalink}")`'
    return f'You can {verb} this file with: `bm_read("{file_path}")`'


def continue_conversation_prompt(
    topic: str, timeframe: str | None, results: Sequence[ContextResult]
) -> str:
    """Build the memory-retrieval prompt for continuing a conversation on `topic`."""
    lines = [
        f"# Continuing conversation on: {topic}",
        "",
        "This is a memory retrieval session.",
        "",
        "The notes below came from Basic Memory. Open a note with `bm_read` or its linked "
        "notes with `bm_context` when a snippet is not enough to respond.",
        "",
        "Here's what I found from previous conversations:",
        "",
    ]

    if not results:
        lines += [
            "The supplied query did not return any information specifically on this topic.",
            "",
            'Try a different search term or check `bm_search(since="7d")`. If the user wants '
            "this topic tracked, offer to start a note with `bm_write`.",
        ]

    for result in results:
        primary = result.primary_result
        lines += [
            "<memory>",
            f"--- memory://{primary.permalink}",
            "",
            f"## {primary.title}",
            f"- **Type**: {primary.type}",
            f"- **Created**: {format_timestamp(primary.created_at)}",
            "",
        ]
        # Relation summaries carry no note text, so only these two get an excerpt.
        if isinstance(primary, (EntitySummary, ObservationSummary)) and primary.content:
            lines += ["**Excerpt**:", "<excerpt>", primary.content, "</excerpt>", ""]

        if result.observations:
            lines.append("## Observations")
            for observation in result.observations:
                lines += [
                    "<observation>",
                    f"- [{observation.category}] {observation.content}",
                    "</observation>",
                ]
            lines.append("")

        lines += [f'You can read this document with: `bm_read("{primary.permalink}")`', ""]

        if result.related_results:
            lines += ["## Related Context", ""]
            for related in result.related_results:
                lines += [
                    "<related>",
                    f"- type: **{related.type}**",
                    f"- title: {related.title}",
                    "",
                    read_hint(related.permalink, related.file_path, verb="view"),
                    "</related>",
                ]
            lines.append("")

        lines += ["</memory>", ""]

    lines += [
        "## Next Steps",
        "<instructions>",
        "You can:",
        f'- Explore more with: `bm_search("{topic}")`',
        f'- See what\'s changed: `bm_search(since="{timeframe or "7d"}")`',
        "",
        "If this conversation produces a decision or insight worth keeping, offer to save "
        "it with `bm_write`.",
        "</instructions>",
    ]
    return "\n".join(lines) + "\n"


def search_prompt(query: str, timeframe: str | None, results: Sequence[SearchResult]) -> str:
    """Build the memory-search prompt for `query` and its results."""
    heading = f'# Search Results for: "{query}"'
    if timeframe:
        heading += f" (after {timeframe})"
    lines = [
        heading,
        "",
        "This is a memory search session.",
        f"I found {len(results)} result(s) that match your query.",
        "",
    ]

    if not results:
        lines += [
            "I couldn't find any results for this query.",
            "",
            "## Other Suggestions",
            "- Try a different search term",
            "- Broaden your search criteria",
            '- Check recent activity with `bm_search(since="1w")`',
            "",
            "If the user wants this topic tracked, offer to start a note with `bm_write`.",
        ]
        return "\n".join(lines) + "\n"

    lines += ["Here are the most relevant results:", ""]
    for index, result in enumerate(results[:SEARCH_PROMPT_RESULT_LIMIT], start=1):
        lines += [f"## {index}. {result.title}", f"- **Type**: {result.type.value}"]
        created_at = (result.metadata or {}).get("created_at")
        if created_at:
            lines.append(f"- **Created**: {format_timestamp(created_at)}")
        lines += [f"- **Relevance Score**: {round(result.score, 2)}", ""]
        if result.content:
            lines += ["- **Excerpt**:", result.content, ""]
        if result.permalink:
            lines += [
                f'You can view this content with: `bm_read("{result.permalink}")`',
                f'Or explore its context with: `bm_context("memory://{result.permalink}")`',
            ]
        else:
            lines.append(f'You can view this file with: `bm_read("{result.file_path}")`')
        lines.append("")

    lines += [
        "## Next Steps",
        "",
        "You can:",
        f'- Refine your search: `bm_search("{query} AND additional_term")`',
        f'- Exclude terms: `bm_search("{query} NOT exclude_term")`',
        f'- View more results: `bm_search("{query}", since=None)`',
        '- Check recent activity: `bm_search(since="7d")`',
        "",
        "If these results add up to something worth keeping, offer to save a synthesis "
        "with `bm_write`.",
    ]
    return "\n".join(lines) + "\n"
