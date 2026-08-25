"""Tests for case-insensitive directory resolution (issue #1326)."""

from basic_memory.utils import resolve_directory_casing


def test_exact_match_keeps_requested_casing():
    """An exactly matching folder is used as given, even beside case-variants."""
    existing = ["Schemas", "schemas", "notes"]
    assert resolve_directory_casing("schemas", existing) == "schemas"
    assert resolve_directory_casing("Schemas", existing) == "Schemas"


def test_unique_case_insensitive_match_adopts_existing_casing():
    existing = ["Schemas", "notes"]
    assert resolve_directory_casing("schemas", existing) == "Schemas"
    assert resolve_directory_casing("SCHEMAS", existing) == "Schemas"
    assert resolve_directory_casing("NOTES", existing) == "notes"


def test_zero_matches_creates_as_given():
    existing = ["Schemas", "notes"]
    assert resolve_directory_casing("research", existing) == "research"
    assert resolve_directory_casing("Research/Drafts", existing) == "Research/Drafts"


def test_multiple_case_variants_keep_requested_casing():
    """Ambiguous case-variant siblings preserve today's exact behavior."""
    existing = ["Schemas", "SCHEMAS", "notes"]
    assert resolve_directory_casing("schemas", existing) == "schemas"


def test_nested_path_resolves_each_segment():
    existing = ["Schemas", "Schemas/Drafts"]
    assert resolve_directory_casing("schemas/drafts", existing) == "Schemas/Drafts"


def test_nested_new_subfolder_under_resolved_parent():
    existing = ["Schemas"]
    assert resolve_directory_casing("schemas/drafts", existing) == "Schemas/drafts"


def test_ambiguous_parent_does_not_splice_other_parent_children():
    """A child under a differently-cased parent must not be adopted once the
    parent segment stayed ambiguous (kept as requested)."""
    existing = ["Schemas", "SCHEMAS", "SCHEMAS/Drafts"]
    assert resolve_directory_casing("schemas/drafts", existing) == "schemas/drafts"


def test_child_resolution_requires_resolved_parent():
    """A case-insensitive whole-path match under a different parent casing is
    ignored: each segment resolves only against children of the resolved parent."""
    existing = ["SCHEMAS", "SCHEMAS/Drafts", "Schemas"]
    # "Schemas" matches exactly, so children resolve under "Schemas" — which has
    # none — leaving the child segment as given.
    assert resolve_directory_casing("Schemas/drafts", existing) == "Schemas/drafts"


def test_root_directory_is_unchanged():
    assert resolve_directory_casing("", ["Schemas"]) == ""


def test_no_existing_directories_keeps_requested():
    assert resolve_directory_casing("schemas", []) == "schemas"
