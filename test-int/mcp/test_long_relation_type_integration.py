"""Integration test for long prose before inline wikilinks.

Issue #721 was originally triggered by markdown bullets that contained inline
`[[wikilinks]]` preceded by long prose. The parser treated all prose before the
wikilink as `relation_type`, and the response model's former `MaxLen(200)`
constraint caused edit_note to fail with:

    1 validation error for EntityResponseV2
    relations.0.relation_type
      String should have at most 200 characters

The relation grammar now fixes the root ambiguity too: unquoted multi-word
prefixes are prose, so this shape should index as a generic `links_to`
relation rather than preserving prose as a custom relation type.
"""

import pytest
from fastmcp import Client

from basic_memory.repository.relation_repository import RelationRepository


@pytest.mark.asyncio
async def test_edit_note_handles_long_prose_around_wikilink(
    mcp_server, app, test_project, engine_factory
):
    """Long prose before an inline wikilink should not become a relation type."""
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
        assert len(write_result.content) == 1
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
        assert len(edit_result.content) == 1
        assert "Edited note (append)" in edit_result.content[0].text

    _, session_maker = engine_factory
    relation_repository = RelationRepository(session_maker, project_id=test_project.id)
    links_to_relations = await relation_repository.find_by_type("links_to")
    prose_type_relations = await relation_repository.find_by_type(long_prose)

    assert any(relation.to_name == "Some Note Title" for relation in links_to_relations)
    assert not prose_type_relations
