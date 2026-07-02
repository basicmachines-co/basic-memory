"""Tests for accepted-note search helpers."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from basic_memory.indexing.accepted_note_search import (
    AcceptedNoteSearchRow,
    accepted_note_content_stems,
    accepted_note_tags,
    accepted_search_content_from_markdown,
    build_accepted_note_search_row,
    first_markdown_h1,
    strip_search_text,
)


def test_accepted_search_content_keeps_legacy_unclosed_frontmatter_searchable() -> None:
    markdown_content = "---\ntitle: legacy\n\n# Body still matters\n"

    assert accepted_search_content_from_markdown(markdown_content) == markdown_content


def test_first_markdown_h1_ignores_fenced_code_blocks() -> None:
    markdown_content = "\n".join(
        [
            "```bash",
            "# not a note title",
            "```",
            "",
            "# Real note title",
            "",
            "Body",
        ]
    )

    assert first_markdown_h1(markdown_content) == "Real note title"


def test_accepted_note_tags_reads_frontmatter_tag_shapes() -> None:
    assert accepted_note_tags({"tags": ["alpha", "beta"]}) == ("alpha", "beta")
    assert accepted_note_tags({"tags": "['alpha', 'beta']"}) == ("alpha", "beta")
    assert accepted_note_tags({"tags": "solo"}) == ("solo",)
    assert accepted_note_tags({"tags": ""}) == ()


def test_accepted_note_content_stems_include_note_identity_text() -> None:
    stems = accepted_note_content_stems(
        title="Project Plan",
        search_content="Main body\x00",
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        tags=("strategy",),
    )

    assert "\x00" not in stems
    assert "Project Plan" in stems
    assert "project" in stems
    assert "Main body" in stems
    assert "notes/project-plan.md" in stems
    assert "strategy" in stems


def test_strip_search_text_treats_missing_values_as_empty_text() -> None:
    assert strip_search_text(None) == ""


def test_build_accepted_note_search_row_returns_immutable_hot_search_state() -> None:
    created_at = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    updated_at = datetime(2026, 6, 18, 13, 0, tzinfo=UTC)

    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan\x00",
        note_type="decision",
        entity_metadata={"tags": "['strategy', 'runtime']"},
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body\x00",
        created_at=created_at,
        updated_at=updated_at,
        project_id=7,
    )

    assert row == AcceptedNoteSearchRow(
        id=42,
        title="Project Plan",
        content_stems=row.content_stems,
        content_snippet="Main body",
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        item_type="entity",
        note_type="decision",
        entity_id=42,
        created_at=created_at,
        updated_at=updated_at,
        project_id=7,
    )
    assert "strategy" in row.content_stems
    assert "\x00" not in row.content_stems

    with pytest.raises(FrozenInstanceError):
        setattr(row, "title", "Changed")
