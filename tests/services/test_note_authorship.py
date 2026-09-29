"""Unit tests for server-stamped note authorship values."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.runtime.job_payloads import RuntimeNoteMaterializationJobPayload
from basic_memory.services.note_authorship import (
    NoteAuthorship,
    normalize_note_author,
    stamp_note_authorship,
)
from basic_memory.services.note_preparation import (
    PreparedEntityFields,
    PreparedEntityWrite,
    rewrite_frontmatter_fields,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Paul Hernandez via Nightly Backup", "Paul Hernandez via Nightly Backup"),
        ("José Núñez", "José Núñez"),
        ("  Paul\n\tHernandez  ", "Paul Hernandez"),
        ("Paul‮Hernandez", "Paul Hernandez"),
        ("​\n ", None),
        (None, None),
    ],
)
def test_normalize_note_author(raw: str | None, expected: str | None) -> None:
    assert normalize_note_author(raw) == expected


def test_normalize_note_author_caps_length() -> None:
    normalized = normalize_note_author("a" * 500)
    assert normalized is not None
    assert len(normalized) == 120


def test_for_write_without_author_stamps_nothing() -> None:
    assert NoteAuthorship.for_write(None, current_markdown=None) is None
    assert NoteAuthorship.for_write("  ", current_markdown="---\ntitle: A\n---\n") is None


def test_for_write_on_create_credits_author_for_both_fields() -> None:
    assert NoteAuthorship.for_write("Paul", current_markdown=None) == NoteAuthorship(
        created_by="Paul", updated_by="Paul"
    )


def test_for_write_on_update_carries_accepted_creator_forward() -> None:
    current = "---\ntitle: A\ncreated_by: Alice\nupdated_by: Alice\n---\n\nBody\n"
    assert NoteAuthorship.for_write("Paul", current_markdown=current) == NoteAuthorship(
        created_by="Alice", updated_by="Paul"
    )


@pytest.mark.parametrize(
    "current",
    [
        "Body without frontmatter\n",
        "---\ntitle: A\n---\n\nBody\n",
        "---\ntitle: [unclosed\n---\n\nBody\n",
        "---\ncreated_by:\n  - not\n  - a name\n---\n\nBody\n",
    ],
)
def test_for_write_on_update_never_invents_a_creator(current: str) -> None:
    assert NoteAuthorship.for_write("Paul", current_markdown=current) == NoteAuthorship(
        created_by=None, updated_by="Paul"
    )


def test_rewrite_frontmatter_fields_removes_keys_and_keeps_body() -> None:
    markdown = "---\ntitle: A\ncreated_by: Forged\n---\n\n  Body with leading spaces  \n"
    rewritten = rewrite_frontmatter_fields(
        markdown,
        updates={"updated_by": "Paul"},
        removals=frozenset({"created_by"}),
    )
    assert "created_by" not in rewritten
    assert "updated_by: Paul" in rewritten
    assert rewritten.endswith("\n\n  Body with leading spaces  \n")


def test_agent_actor_kind_survives_materialization_payload() -> None:
    payload = RuntimeNoteMaterializationJobPayload(
        project_id=1,
        entity_id=2,
        db_version=3,
        db_checksum="abc123",
        actor_kind="agent",
        actor_name="Nightly Backup",
    )
    assert payload.actor_kind == "agent"


def test_stamp_leaves_malformed_frontmatter_untouched() -> None:
    markdown = "---\nnot: [yaml\n---\n\nBody\n"
    now = datetime.now(tz=UTC)
    prepared = PreparedEntityWrite(
        file_path=Path("notes/a.md"),
        markdown_content=markdown,
        search_content=markdown,
        entity_fields=PreparedEntityFields(
            title="a",
            note_type="note",
            entity_metadata=None,
            content_type="text/markdown",
            permalink="notes/a",
            file_path="notes/a.md",
            created_at=now,
            updated_at=now,
        ),
        entity_markdown=EntityMarkdown(
            frontmatter=EntityFrontmatter(metadata={}),
            frontmatter_state="malformed",
        ),
    )
    authorship = NoteAuthorship(created_by="Paul", updated_by="Paul")
    assert stamp_note_authorship(prepared, authorship) is prepared
