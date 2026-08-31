"""Tests for structural section scanning and read-time slicing (SPEC-47 / #1403).

Pure-function tests over dedented strings: no fixtures, no I/O. The scanner and
slicers are re-run at read time on the exact content being served, so these
boundary cases pin canonical-content correctness.
"""

from textwrap import dedent

import pytest

from basic_memory.markdown.sections import (
    LineRange,
    NoteSlice,
    NoteSliceError,
    TRUNCATION_MARKER_TEMPLATE,
    _frontmatter_line_count,
    _token_budget_line_count,
    parse_line_range,
    parse_section_selector,
    scan_sections,
    slice_note_content,
)

# --- scan_sections ---


def test_scan_empty_body_returns_no_sections():
    assert scan_sections("") == ()


def test_scan_body_without_headings_returns_no_sections():
    assert scan_sections("just prose\n\nmore prose\n") == ()


def test_scan_nested_headings_build_paths_and_nested_spans():
    body = dedent("""\
        # Top
        intro
        ## Sub
        sub body
        ## Sub2
        more
        # Next
        tail""")

    sections = scan_sections(body)

    by_heading = {section.heading: section for section in sections}
    assert [section.heading for section in sections] == ["Top", "Sub", "Sub2", "Next"]

    top = by_heading["Top"]
    assert top.level == 1
    assert top.path == ("Top",)
    assert top.heading_path == "Top"
    assert (top.start_line, top.end_line) == (1, 6)

    sub = by_heading["Sub"]
    assert sub.level == 2
    assert sub.path == ("Top", "Sub")
    assert sub.heading_path == "Top/Sub"
    assert (sub.start_line, sub.end_line) == (3, 4)

    sub2 = by_heading["Sub2"]
    assert sub2.path == ("Top", "Sub2")
    assert (sub2.start_line, sub2.end_line) == (5, 6)

    nxt = by_heading["Next"]
    assert nxt.path == ("Next",)
    assert (nxt.start_line, nxt.end_line) == (7, 8)

    # Subsections nest inside their parent: overlapping spans are correct.
    assert top.start_line <= sub.start_line and sub.end_line <= top.end_line


def test_scan_skipped_heading_level_keeps_ancestor_path():
    body = "# A\n### Deep\ncontent\n"

    sections = scan_sections(body)

    assert sections[1].path == ("A", "Deep")
    assert sections[1].level == 3


def test_scan_duplicate_headings_same_path_count_duplicate_index():
    body = dedent("""\
        # Spec
        ## Auth
        first
        ## Auth
        second""")

    sections = scan_sections(body)

    auths = [section for section in sections if section.heading == "Auth"]
    assert [section.duplicate_index for section in auths] == [0, 1]
    assert [section.path for section in auths] == [("Spec", "Auth"), ("Spec", "Auth")]
    assert (auths[0].start_line, auths[0].end_line) == (2, 3)
    assert (auths[1].start_line, auths[1].end_line) == (4, 5)


def test_scan_same_heading_under_different_parents_gets_distinct_paths():
    body = dedent("""\
        # Auth
        ## Decisions
        a
        # Ops
        ## Decisions
        b""")

    sections = scan_sections(body)

    decisions = [section for section in sections if section.heading == "Decisions"]
    assert [section.heading_path for section in decisions] == ["Auth/Decisions", "Ops/Decisions"]
    assert [section.duplicate_index for section in decisions] == [0, 0]


def test_scan_ignores_headings_inside_fenced_code_blocks():
    body = dedent("""\
        # Real
        ```
        # not a heading
        ```
        ~~~
        ## also not one
        ~~~
        done""")

    sections = scan_sections(body)

    assert [section.heading for section in sections] == ["Real"]
    assert sections[0].end_line == 8


def test_scan_setext_headings_span_their_underline():
    body = dedent("""\
        Title
        =====
        body

        Sub
        ---
        more""")

    sections = scan_sections(body)

    assert [(section.heading, section.level) for section in sections] == [("Title", 1), ("Sub", 2)]
    title, sub = sections
    assert (title.start_line, title.end_line) == (1, 7)
    assert title.path == ("Title",)
    assert (sub.start_line, sub.end_line) == (5, 7)
    assert sub.path == ("Title", "Sub")


def test_scan_empty_section_ends_on_its_own_heading_line():
    body = "# A\n# B"

    sections = scan_sections(body)

    a, b = sections
    assert (a.start_line, a.end_line) == (1, 1)
    assert (b.start_line, b.end_line) == (2, 2)


def test_scan_byte_offsets_round_trip_unicode_content():
    body = "# Héllo ☕\ncafé costs 5€\n# End\nfin"

    sections = scan_sections(body)

    encoded = body.encode("utf-8")
    hello, end = sections
    assert encoded[hello.start_offset : hello.end_offset].decode("utf-8") == (
        "# Héllo ☕\ncafé costs 5€\n"
    )
    assert encoded[end.start_offset : end.end_offset].decode("utf-8") == "# End\nfin"
    assert end.end_offset == len(encoded)


def test_scan_trailing_newline_is_included_in_final_section_bytes():
    body = "# Only\ntext\n"

    (only,) = scan_sections(body)

    assert (only.start_line, only.end_line) == (1, 2)
    assert body.encode("utf-8")[only.start_offset : only.end_offset].decode("utf-8") == body


def test_scan_lone_carriage_returns_count_as_line_boundaries():
    # markdown-it normalizes "\r\n?|\n" to "\n" before assigning token line maps,
    # so a lone \r is a line boundary; counting by split("\n") shifted every later
    # heading and indexed past the line table (issue #1403 review).
    body = "# One\ralpha\r# Two\rbeta"

    one, two = scan_sections(body)

    assert (one.start_line, one.end_line) == (1, 2)
    assert (two.start_line, two.end_line) == (3, 4)
    encoded = body.encode("utf-8")
    assert encoded[one.start_offset : one.end_offset].decode("utf-8") == "# One\ralpha\r"
    assert encoded[two.start_offset : two.end_offset].decode("utf-8") == "# Two\rbeta"
    assert two.end_offset == len(encoded)


def test_scan_crlf_terminators_round_trip_original_bytes():
    body = "# A\r\nbody\r\n# B\r\ntail\r\n"

    a, b = scan_sections(body)

    assert (a.start_line, a.end_line) == (1, 2)
    assert (b.start_line, b.end_line) == (3, 4)
    encoded = body.encode("utf-8")
    assert encoded[a.start_offset : a.end_offset].decode("utf-8") == "# A\r\nbody\r\n"
    assert encoded[b.start_offset : b.end_offset].decode("utf-8") == "# B\r\ntail\r\n"
    assert b.end_offset == len(encoded)


# --- parse_section_selector ---


@pytest.mark.parametrize(
    ("selector", "segments", "duplicate_index"),
    [
        ("Decisions", ("Decisions",), 0),
        ("Auth/Decisions", ("Auth", "Decisions"), 0),
        ("Heading[1]", ("Heading",), 1),
        ("Auth/Decisions[2]", ("Auth", "Decisions"), 2),
        ("## Auth", ("Auth",), 0),
        ("# Auth / ### Decisions ", ("Auth", "Decisions"), 0),
        ("#tag", ("#tag",), 0),
    ],
)
def test_parse_section_selector_accepted_forms(selector, segments, duplicate_index):
    parsed = parse_section_selector(selector)

    assert parsed is not None
    assert parsed.raw == selector
    assert parsed.segments == segments
    assert parsed.duplicate_index == duplicate_index


@pytest.mark.parametrize("selector", ["", "   ", "A//B", "/Auth", "Auth/", "[2]", "A/ /B"])
def test_parse_section_selector_rejects_malformed_forms(selector):
    assert parse_section_selector(selector) is None


# --- parse_line_range ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2-4", LineRange(start=2, end=4)),
        ("3-", LineRange(start=3, end=None)),
        ("5", LineRange(start=5, end=5)),
        (" 2 - 4 ", LineRange(start=2, end=4)),
        ("7-7", LineRange(start=7, end=7)),
    ],
)
def test_parse_line_range_accepted_forms(value, expected):
    assert parse_line_range(value) == expected


@pytest.mark.parametrize("value", ["", "0", "-5", "a", "2-1", "1-b", "1.5-2", "0-"])
def test_parse_line_range_rejects_malformed_forms(value):
    assert parse_line_range(value) is None


# --- _frontmatter_line_count ---


def test_frontmatter_line_count_counts_well_formed_block():
    text = "---\ntitle: T\ntags: [a]\n---\n\nbody"
    assert _frontmatter_line_count(text) == 4


def test_frontmatter_line_count_zero_without_opening_fence():
    assert _frontmatter_line_count("# Heading\n---\n") == 0
    assert _frontmatter_line_count("") == 0


def test_frontmatter_line_count_zero_for_unterminated_block():
    assert _frontmatter_line_count("---\ntitle: T\nbody without closing fence") == 0


def test_frontmatter_line_count_counts_carriage_return_terminated_block():
    # Lone \r terminators count as line boundaries, matching the document line
    # table the count indexes into.
    assert _frontmatter_line_count("---\rtitle: T\r---\rbody") == 3


# --- slice_note_content: section selection ---

_DOC = dedent("""\
    ---
    title: Spec
    ---

    # Auth
    line a
    ## Decisions
    d1
    d2
    ## Ops
    o1
    # Tail
    z""")


def _slice(full_text: str, **kwargs) -> NoteSlice:
    result = slice_note_content(full_text, **kwargs)
    assert isinstance(result, NoteSlice)
    return result


def _slice_error(full_text: str, **kwargs) -> NoteSliceError:
    result = slice_note_content(full_text, **kwargs)
    assert isinstance(result, NoteSliceError)
    return result


def test_slice_by_section_returns_document_absolute_span():
    selector = parse_section_selector("Decisions")
    assert selector is not None

    sliced = _slice(_DOC, section=selector, note_title="Spec")

    assert sliced.content == "## Decisions\nd1\nd2"
    assert sliced.section == "Auth/Decisions"
    assert (sliced.start_line, sliced.end_line) == (7, 9)
    assert sliced.total_lines == 13
    assert not sliced.truncated
    assert sliced.continue_line is None
    # The returned coordinates address the same lines in the full document.
    assert "\n".join(_DOC.split("\n")[6:9]) == sliced.content


def test_slice_by_qualified_path_disambiguates():
    doc = dedent("""\
        # Auth
        ## Decisions
        a
        # Ops
        ## Decisions
        b""")
    selector = parse_section_selector("Ops/Decisions")
    assert selector is not None

    sliced = _slice(doc, section=selector)

    assert sliced.content == "## Decisions\nb"
    assert sliced.section == "Ops/Decisions"
    assert (sliced.start_line, sliced.end_line) == (5, 6)


def test_slice_ambiguous_suffix_lists_qualified_paths():
    doc = "# Auth\n## Decisions\na\n# Ops\n## Decisions\nb"
    selector = parse_section_selector("Decisions")
    assert selector is not None

    error = _slice_error(doc, section=selector, note_title="Spec")

    assert "'Decisions' is ambiguous in note 'Spec'" in error.message
    assert "Auth/Decisions, Ops/Decisions" in error.message


def test_slice_duplicate_headings_addressed_by_bracket_index():
    doc = dedent("""\
        # Spec
        ## Auth
        first
        ## Auth
        second""")
    selector = parse_section_selector("Auth[1]")
    assert selector is not None

    sliced = _slice(doc, section=selector)

    assert sliced.content == "## Auth\nsecond"
    assert sliced.section == "Spec/Auth[1]"
    assert (sliced.start_line, sliced.end_line) == (4, 5)


def test_slice_duplicate_index_out_of_range_reports_count():
    doc = "# Spec\n## Auth\nfirst\n## Auth\nsecond"
    selector = parse_section_selector("Auth[5]")
    assert selector is not None

    error = _slice_error(doc, section=selector)

    assert "2 section(s) share the path 'Spec/Auth'" in error.message
    assert "duplicate indexes 0-1" in error.message


def test_slice_unknown_section_lists_available_headings_with_duplicates_bracketed():
    doc = "# Spec\n## Auth\nfirst\n## Auth\nsecond\n## Ops\nz"
    selector = parse_section_selector("Nope")
    assert selector is not None

    error = _slice_error(doc, section=selector, note_title="Spec")

    assert "'Nope' not found in note 'Spec'" in error.message
    assert "Available sections: Spec, Spec/Auth[0], Spec/Auth[1], Spec/Ops" in error.message


def test_slice_unknown_section_in_heading_less_note():
    selector = parse_section_selector("Anything")
    assert selector is not None

    error = _slice_error("plain prose only", section=selector)

    assert "(note has no headings)" in error.message
    assert " in note " not in error.message


def test_slice_by_section_after_stray_carriage_return_serves_the_heading():
    # Regression: split("\n") counting let a stray \r shift the section span so
    # this selector silently served only "tail" (issue #1403 review).
    selector = parse_section_selector("H")
    assert selector is not None

    sliced = _slice("one\rtwo\n# H\ntail", section=selector)

    assert sliced.content == "# H\ntail"
    assert (sliced.start_line, sliced.end_line) == (3, 4)
    assert sliced.total_lines == 4


# --- slice_note_content: line ranges ---


def test_slice_by_line_range_addresses_full_document():
    sliced = _slice(_DOC, lines=LineRange(start=2, end=4))

    assert sliced.content == "title: Spec\n---\n"
    assert (sliced.start_line, sliced.end_line) == (2, 4)
    assert sliced.total_lines == 13
    assert sliced.section is None


def test_slice_open_ended_line_range_runs_to_end():
    sliced = _slice(_DOC, lines=LineRange(start=12, end=None))

    assert sliced.content == "# Tail\nz"
    assert (sliced.start_line, sliced.end_line) == (12, 13)


def test_slice_line_range_end_clamps_to_total_lines():
    sliced = _slice(_DOC, lines=LineRange(start=13, end=999))

    assert sliced.content == "z"
    assert (sliced.start_line, sliced.end_line) == (13, 13)


def test_slice_single_line_range():
    sliced = _slice(_DOC, lines=LineRange(start=5, end=5))

    assert sliced.content == "# Auth"


def test_slice_empty_document_yields_empty_slice():
    sliced = _slice("", lines=LineRange(start=1, end=None))

    assert sliced.content == ""
    assert sliced.total_lines == 0


def test_slice_section_and_lines_are_mutually_exclusive():
    selector = parse_section_selector("Auth")
    assert selector is not None
    with pytest.raises(ValueError, match="mutually exclusive"):
        slice_note_content(_DOC, section=selector, lines=LineRange(start=1, end=2))


def test_slice_requires_at_least_one_slice_parameter():
    with pytest.raises(ValueError, match="requires section, lines, or max_tokens"):
        slice_note_content(_DOC)


# --- slice_note_content: max_tokens ---


def test_slice_max_tokens_alone_serves_body_after_frontmatter():
    sliced = _slice(_DOC, max_tokens=10_000)

    assert sliced.content == "\n".join(_DOC.split("\n")[3:])
    assert (sliced.start_line, sliced.end_line) == (4, 13)
    assert not sliced.truncated
    assert sliced.continue_line is None


def test_slice_max_tokens_cuts_at_section_boundary_with_marker():
    # Body: one small section, then a second section; a budget that fits the
    # first section but not both must cut exactly at the second heading.
    doc = "# One\n" + "a" * 40 + "\n# Two\n" + "b" * 400
    sliced = _slice(doc, max_tokens=20)  # budget: 80 chars

    kept_lines = sliced.content.split("\n")
    assert kept_lines[-1] == TRUNCATION_MARKER_TEMPLATE.format(max_tokens=20, continue_line=3)
    assert kept_lines[:-1] == ["# One", "a" * 40]
    assert sliced.truncated
    assert sliced.continue_line == 3
    assert (sliced.start_line, sliced.end_line) == (1, 2)


def test_slice_max_tokens_continue_read_reconstructs_document():
    doc = "# One\n" + "a" * 40 + "\n# Two\n" + "b" * 40
    sliced = _slice(doc, max_tokens=20)
    assert sliced.truncated and sliced.continue_line is not None

    remainder = _slice(doc, lines=LineRange(start=sliced.continue_line, end=None))

    kept = "\n".join(sliced.content.split("\n")[:-1])
    assert kept + "\n" + remainder.content == doc


def test_slice_max_tokens_falls_back_to_paragraph_boundary():
    # No section boundary after the start: the cut lands on the blank line
    # between paragraphs instead.
    doc = "# Only\n" + "a" * 40 + "\n\n" + "b" * 400
    sliced = _slice(doc, max_tokens=20)  # budget: 80 chars

    kept_lines = sliced.content.split("\n")
    assert kept_lines[:-1] == ["# Only", "a" * 40]
    assert sliced.truncated
    assert sliced.continue_line == 3


def test_slice_max_tokens_line_backstop_within_one_paragraph():
    # One giant paragraph: the backstop cuts at the last whole line in budget.
    doc = "x" * 30 + "\n" + "y" * 30 + "\n" + "z" * 30
    sliced = _slice(doc, max_tokens=20)  # budget: 80 chars, fits two lines (61)

    kept_lines = sliced.content.split("\n")
    assert kept_lines[:-1] == ["x" * 30, "y" * 30]
    assert sliced.continue_line == 3


def test_slice_max_tokens_applies_after_section_selection():
    doc = "# Big\n" + "a" * 40 + "\n\n" + "b" * 400 + "\n# Other\nz"
    selector = parse_section_selector("Big")
    assert selector is not None

    sliced = _slice(doc, section=selector, max_tokens=20)

    kept_lines = sliced.content.split("\n")
    assert kept_lines[:-1] == ["# Big", "a" * 40]
    assert sliced.section == "Big"
    assert sliced.truncated
    assert sliced.continue_line == 3
    assert sliced.end_line == 2


def test_slice_max_tokens_within_budget_is_not_truncated():
    sliced = _slice("# A\nshort", max_tokens=100)

    assert sliced.content == "# A\nshort"
    assert not sliced.truncated


# --- _token_budget_line_count backstops ---


def test_token_budget_no_cut_when_text_fits():
    assert _token_budget_line_count("short", ["short"], budget_chars=100) is None


def test_token_budget_single_over_budget_line_is_served_whole():
    text = "x" * 500
    assert _token_budget_line_count(text, [text], budget_chars=10) is None


def test_token_budget_keeps_first_line_even_when_it_exceeds_budget():
    lines = ["x" * 500, "tail"]
    assert _token_budget_line_count("\n".join(lines), lines, budget_chars=10) == 1
