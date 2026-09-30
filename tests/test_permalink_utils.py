"""Tests for canonical permalink utility helpers."""

from basic_memory.utils import (
    build_permalink_resolution_candidates,
    build_qualified_permalink_reference,
)


def test_workspace_qualified_permalink_candidates_include_legacy_without_rewrapping():
    candidates = build_permalink_resolution_candidates(
        "personal/main/notes/example",
        "main",
        workspace_permalink="personal",
    )

    assert candidates == [
        "personal/main/notes/example",
        "main/notes/example",
        "notes/example",
    ]


def test_project_prefixed_candidate_includes_short_legacy_when_project_prefix_disabled():
    candidates = build_permalink_resolution_candidates(
        "main/notes/example",
        "main",
        include_project=False,
    )

    assert candidates == [
        "main/notes/example",
        "notes/example",
    ]


def test_short_permalink_candidates_include_workspace_and_project_forms():
    candidates = build_permalink_resolution_candidates(
        "notes/example",
        "main",
        workspace_permalink="personal",
    )

    assert candidates == [
        "notes/example",
        "personal/main/notes/example",
        "main/notes/example",
    ]


def test_short_workspace_candidate_keeps_project_legacy_when_project_prefix_disabled():
    candidates = build_permalink_resolution_candidates(
        "notes/example",
        "main",
        include_project=False,
        workspace_permalink="personal",
    )

    assert candidates == [
        "notes/example",
        "personal/main/notes/example",
        "main/notes/example",
    ]


def test_qualified_permalink_reference_preserves_lookup_syntax():
    assert (
        build_qualified_permalink_reference(
            "main",
            "notes/roadmap.md",
            include_project=False,
        )
        == "notes/roadmap.md"
    )
    assert (
        build_qualified_permalink_reference(
            "main",
            "patterns/*",
            include_project=True,
        )
        == "main/patterns/*"
    )
    assert (
        build_qualified_permalink_reference(
            "main",
            "patterns/*",
            include_project=True,
            workspace_permalink="personal",
        )
        == "personal/main/patterns/*"
    )


def test_prefixed_candidates_keep_the_callers_spelling_of_the_remainder():
    """An explicit frontmatter permalink is stored verbatim, not as its slug (#1549)."""
    expected_remainders = ["s/ses_AbCdEf", "s/ses-ab-cd-ef"]

    project_prefixed = build_permalink_resolution_candidates(
        "main/s/ses_AbCdEf", "main", include_project=True
    )
    assert project_prefixed == ["main/s/ses_AbCdEf", "main/s/ses-ab-cd-ef", *expected_remainders]

    unprefixed_route = build_permalink_resolution_candidates(
        "main/s/ses_AbCdEf", "main", include_project=False
    )
    assert unprefixed_route == ["main/s/ses_AbCdEf", "main/s/ses-ab-cd-ef", *expected_remainders]

    workspace_qualified = build_permalink_resolution_candidates(
        "personal/main/s/ses_AbCdEf", "main", workspace_permalink="personal"
    )
    assert workspace_qualified == [
        "personal/main/s/ses_AbCdEf",
        "personal/main/s/ses-ab-cd-ef",
        "main/s/ses_AbCdEf",
        "main/s/ses-ab-cd-ef",
        *expected_remainders,
    ]


def test_non_markdown_identifiers_keep_their_extension():
    """Resource entities have no permalink, so `.txt` must not collapse to the .md stem (#1629)."""
    assert build_permalink_resolution_candidates("main/notes/foo.txt", "main") == [
        "main/notes/foo.txt"
    ]
    assert "main/notes/foo" in build_permalink_resolution_candidates("main/notes/foo.md", "main")
    # A version-like title is not a file extension.
    assert "main/release-2.0" in build_permalink_resolution_candidates("Release 2.0", "main")
