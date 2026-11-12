"""Prompt text for the v2 prompt routes, built without a template engine (#2044)."""

from datetime import datetime

from basic_memory.api.prompt_text import (
    SEARCH_PROMPT_RESULT_LIMIT,
    continue_conversation_prompt,
    format_timestamp,
    search_prompt,
)
from basic_memory.schemas.memory import (
    ContextResult,
    EntitySummary,
    ObservationSummary,
    RelationSummary,
)
from basic_memory.schemas.search import SearchItemType, SearchResult

CREATED = datetime(2026, 11, 1, 9, 30)


def entity(permalink: str | None, *, content: str | None = None) -> EntitySummary:
    return EntitySummary(
        external_id="ext-1",
        permalink=permalink,
        title=f"Title {permalink}",
        content=content,
        file_path=f"notes/{permalink or 'image'}.md",
        created_at=CREATED,
    )


def test_format_timestamp_handles_datetimes_iso_strings_and_other_values():
    assert format_timestamp(CREATED) == "2026-11-01 09:30"
    assert format_timestamp("2026-11-01T09:30:00") == "2026-11-01 09:30"
    assert format_timestamp("last tuesday") == "last tuesday"
    assert format_timestamp(42) == "42"


def test_continue_conversation_prompt_renders_context_and_related_items():
    observation = ObservationSummary(
        file_path="notes/plan.md",
        permalink="notes/plan",
        category="decision",
        content="Ship on Friday",
        created_at=CREATED,
    )
    result = ContextResult(
        primary_result=entity("notes/plan", content="Plan body"),
        observations=[observation],
        related_results=[entity("notes/other"), entity(None)],
    )

    prompt = continue_conversation_prompt("plan", "2d", [result])

    assert prompt.startswith("# Continuing conversation on: plan\n")
    assert "--- memory://notes/plan" in prompt
    assert "- **Created**: 2026-11-01 09:30" in prompt
    assert "<excerpt>\nPlan body\n</excerpt>" in prompt
    assert "- [decision] Ship on Friday" in prompt
    assert 'You can view this document with: `bm_read("notes/other")`' in prompt
    assert 'You can view this file with: `bm_read("notes/image.md")`' in prompt
    assert '`bm_search(since="2d")`' in prompt
    assert "did not return any information" not in prompt


def test_continue_conversation_prompt_skips_excerpt_for_relations_and_handles_no_results():
    relation = RelationSummary(
        title="plan links other",
        file_path="notes/plan.md",
        permalink="notes/plan/links/other",
        relation_type="links",
        created_at=CREATED,
    )

    with_relation = continue_conversation_prompt(
        "plan", None, [ContextResult(primary_result=relation)]
    )
    empty = continue_conversation_prompt("plan", None, [])

    assert "**Excerpt**" not in with_relation
    assert '`bm_search(since="7d")`' in with_relation
    assert "did not return any information specifically on this topic" in empty


def test_search_prompt_lists_top_results_with_hints():
    note = SearchResult(
        title="Plan",
        type=SearchItemType.ENTITY,
        score=0.87654,
        permalink="notes/plan",
        content="Plan body",
        file_path="notes/plan.md",
        metadata={"created_at": "2026-11-01T09:30:00"},
    )
    file_hit = SearchResult(
        title="Diagram",
        type=SearchItemType.ENTITY,
        score=0.5,
        permalink=None,
        file_path="images/diagram.png",
    )
    extras = [note.model_copy(update={"title": f"Extra {n}"}) for n in range(5)]

    prompt = search_prompt("plan", "7d", [note, file_hit, *extras])

    assert prompt.startswith('# Search Results for: "plan" (after 7d)\n')
    assert "I found 7 result(s)" in prompt
    assert "## 1. Plan\n- **Type**: entity\n- **Created**: 2026-11-01 09:30" in prompt
    assert "- **Relevance Score**: 0.88" in prompt
    assert "- **Excerpt**:\nPlan body" in prompt
    assert '`bm_context("memory://notes/plan")`' in prompt
    assert 'You can view this file with: `bm_read("images/diagram.png")`' in prompt
    assert f"## {SEARCH_PROMPT_RESULT_LIMIT}. Extra 2" in prompt
    assert "Extra 3" not in prompt


def test_search_prompt_inserts_query_text_as_a_value():
    """Query text that looks like template syntax comes back verbatim."""
    prompt = search_prompt("{{#each x}}{{this}}{{/each}}", None, [])

    assert prompt.startswith('# Search Results for: "{{#each x}}{{this}}{{/each}}"\n')
    assert "I couldn't find any results for this query." in prompt
