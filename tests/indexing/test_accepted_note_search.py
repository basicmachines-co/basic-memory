"""Tests for accepted-note search helpers."""

from basic_memory.indexing.accepted_note_search import (
    accepted_note_content_stems,
    accepted_note_tags,
    accepted_search_content_from_markdown,
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
