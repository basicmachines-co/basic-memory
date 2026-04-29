"""
Integration test for long relation_type values (regression guard for issue #721).

When a markdown bullet contains an inline `[[wikilink]]` preceded by long prose,
`parse_relation()` extracts ALL of that prose as the `relation_type`. Previously
the response model `RelationType` had a `MaxLen(200)` constraint that caused
edit_note (which round-trips through the response model when re-indexing) to
fail with:

    1 validation error for EntityResponseV2
    relations.0.relation_type
      String should have at most 200 characters

Commit 01cbad1d removed the cap from `RelationType`. This test locks in that
fix so a future contributor reintroducing `MaxLen` will see the test fail
before shipping it.

Out of scope: improving `parse_relation()` to fall back to a default relation
type when the prose-before-link looks like a sentence rather than a label.
That's a knowledge-graph-quality improvement, not a correctness fix, and is
not required to keep edit_note working.
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_edit_note_handles_long_prose_around_wikilink(mcp_server, app, test_project):
    """edit_note must succeed on notes whose inline wikilinks have >200 chars
    of prose preceding them — that prose becomes the parsed relation_type."""
    long_prose = (
        "**Lorem ipsum dolor sit amet** — consectetur adipiscing elit, sed do eiusmod "
        "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, "
        "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
        "consequat. Trust boundary model documented in"
    )
    assert len(long_prose) > 200, (
        f"setup wrong: prose-before-link must exceed the historical 200-char cap, "
        f"got {len(long_prose)} chars"
    )

    note_body = (
        "# Long Relation Type Repro\n\n"
        f"- {long_prose} [[Some Note Title]] for additional context.\n"
    )

    async with Client(mcp_server) as client:
        # Create the note (file-write side; would already fail at index time
        # if RelationType MaxLen were back).
        write_result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Long Relation Type Repro",
                "directory": "issue721",
                "content": note_body,
            },
        )
        write_text = write_result.content[0].text
        assert "Created note" in write_text or "Updated note" in write_text

        # Edit the note. This triggers re-index → response model validation.
        # With the historical MaxLen(200) cap, this would raise:
        #   "relations.0.relation_type String should have at most 200 characters"
        edit_result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "Long Relation Type Repro",
                "operation": "append",
                "content": "\n\nappended line\n",
            },
        )
        assert "Edited note" in edit_result.content[0].text
