"""Section edits share the read selector and keep the section's layout (SPEC-35.2, #1815)."""

import pytest

from basic_memory.services.note_preparation import (
    apply_edit_operation,
    insert_relative_to_section,
    replace_section_content,
)

TABLE_NOTE = "## Table\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Notes\n"


def test_replace_section_keeps_blank_lines_around_the_body():
    """#1815: the blank line under the heading and before the next one survive."""
    result = replace_section_content(TABLE_NOTE, "## Table", "| x | y |\n|---|---|\n| 9 | 8 |")

    assert result == "## Table\n\n| x | y |\n|---|---|\n| 9 | 8 |\n\n## Notes\n"


def test_replace_section_trims_the_replacement_edges():
    result = replace_section_content(TABLE_NOTE, "Table", "\n\nnew body\n\n")

    assert result == "## Table\n\nnew body\n\n## Notes\n"


def test_replace_section_keeps_a_tight_layout_tight():
    result = replace_section_content("## A\nold\n## B\n", "A", "new")

    assert result == "## A\nnew\n## B\n"


def test_replacement_that_restates_the_heading_keeps_one_heading():
    result = replace_section_content(TABLE_NOTE, "Table", "## Table\n\nnew")

    assert result == "## Table\n\nnew\n\n## Notes\n"


def test_section_path_selects_a_nested_heading():
    note = "# Auth\n## Decisions\nauth old\n# Billing\n## Decisions\nbilling old\n"

    result = replace_section_content(note, "Billing/Decisions", "billing new")

    assert result == "# Auth\n## Decisions\nauth old\n# Billing\n## Decisions\nbilling new\n"


def test_duplicate_headings_need_an_index():
    note = "## Log\none\n## Log\ntwo\n"

    with pytest.raises(ValueError, match=r"Multiple sections found.*Log\[0\], Log\[1\]"):
        replace_section_content(note, "Log", "x")
    assert replace_section_content(note, "Log[1]", "2") == "## Log\none\n## Log\n2\n"


def test_ambiguous_paths_are_refused():
    note = "# Auth\n## Decisions\na\n# Billing\n## Decisions\nb\n"

    with pytest.raises(ValueError, match="ambiguous"):
        replace_section_content(note, "Decisions", "x")


def test_missing_section_lists_the_available_ones():
    with pytest.raises(ValueError, match="not found. Available sections: Table, Notes"):
        replace_section_content(TABLE_NOTE, "Nope", "x")


def test_setext_heading_is_a_section():
    note = "Intro\n=====\nold\n# Next\n"

    assert replace_section_content(note, "Intro", "new") == "Intro\n=====\nnew\n# Next\n"
    assert (
        insert_relative_to_section(note, "Intro", "first", "after")
        == "Intro\n=====\nfirst\n\nold\n# Next\n"
    )


def test_frontmatter_lines_are_counted():
    note = "---\ntitle: T\n---\n\n## A\nold\n"

    assert replace_section_content(note, "A", "new") == "---\ntitle: T\n---\n\n## A\nnew\n"


def test_replace_without_subsections_stops_at_the_first_child():
    note = "## A\nold\n### A1\nchild\n## B\n"

    result = replace_section_content(note, "A", "new", replace_subsections=False)

    assert result == "## A\nnew\n### A1\nchild\n## B\n"


def test_insert_before_uses_the_selector():
    note = "# Top\n## Log\nentry\n"

    assert insert_relative_to_section(note, "Top/Log", "added", "before") == (
        "# Top\n\nadded\n\n## Log\nentry\n"
    )


def test_prepend_leaves_a_blank_line_before_a_heading():
    """#1815: prepended text no longer runs into the note's opening heading."""
    assert apply_edit_operation("# Title\nbody", "prepend", "Line.") == "Line.\n\n# Title\nbody"
    assert apply_edit_operation("# Title\nbody", "prepend", "Line.\n") == "Line.\n\n# Title\nbody"
    assert (
        apply_edit_operation("---\ntitle: T\n---\n\n# Title\n", "prepend", "Line.")
        == "---\ntitle: T\n---\n\nLine.\n\n# Title"
    )
    # A body that opens with text keeps the existing single-newline join.
    assert apply_edit_operation("body", "prepend", "- item") == "- item\nbody"


# --- Line terminators ---


def test_lone_carriage_returns_address_the_same_lines_as_the_scanner():
    """A lone \\r ends a line for the scanner, so the edit must split there too."""
    note = "## A\rold\r## B\rkeep"

    assert replace_section_content(note, "A", "new") == "## A\rnew\r## B\rkeep"
    assert insert_relative_to_section(note, "B", "first", "after") == (
        "## A\rold\r## B\rfirst\r\rkeep"
    )


def test_crlf_lines_outside_the_edit_keep_their_bytes():
    note = "## A\r\nold\r\n## B\r\nkeep\r\n"

    assert replace_section_content(note, "A", "new") == "## A\r\nnew\r\n## B\r\nkeep\r\n"


def test_an_edit_at_the_end_keeps_the_final_newline_state():
    assert replace_section_content("## A\nold", "A", "new") == "## A\nnew"
    assert replace_section_content("## A\nold\n", "A", "new") == "## A\nnew\n"
    assert insert_relative_to_section("## A", "A", "first", "after") == "## A\nfirst"


def test_restated_heading_in_any_spelling_is_dropped():
    """A replacement copied from bm_read may restate the heading with closing hashes."""
    note = "## Decisions\nold\n## Next\n"

    assert replace_section_content(note, "Decisions", "## Decisions ##\nnew") == (
        "## Decisions\nnew\n## Next\n"
    )
    setext = "Decisions\n---------\nold\n## Next\n"
    assert replace_section_content(setext, "Decisions", "Decisions\n---\nnew") == (
        "Decisions\n---------\nnew\n## Next\n"
    )
