"""Line coordinates and bounded match windows, including Markdown newline semantics."""

import pytest

from basic_memory.markdown.line_scanning import format_line_read, scan_literal_lines
from basic_memory.markdown.sections import document_lines


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", []),
        ("\n", [""]),
        ("a\r\nb\rc\n", ["a", "b", "c"]),
        ("a\u2028b\nc", ["a\u2028b", "c"]),
        ("a\n\n", ["a", ""]),
    ],
)
def test_document_lines(content: str, expected: list[str]) -> None:
    assert document_lines(content) == expected


def test_literal_windows_merge_and_limit() -> None:
    scan = scan_literal_lines(
        "retry\ncontext\nRETRY\nother\nother\nother\nretry\n",
        "retry",
        context_lines=1,
        max_matches=2,
    )
    assert scan.total_lines == 7
    assert scan.match_count == 3
    assert scan.next_match_line == 7
    assert len(scan.windows) == 1
    window = scan.windows[0]
    assert (window.start_line, window.end_line, window.match_lines) == (1, 4, [1, 3])
    assert window.content == "retry\ncontext\nRETRY\nother"


def test_separate_windows_casefold_and_literal_punctuation() -> None:
    scan = scan_literal_lines(
        "STRASSE.*\nignore\nignore\nStraße.*", "straße.*", context_lines=0, max_matches=10
    )
    assert [window.match_lines for window in scan.windows] == [[1], [4]]
    assert scan.next_match_line is None
    assert scan.match_count == 2


@pytest.mark.parametrize("content", ["", "something else\n"])
def test_no_literal_match(content: str) -> None:
    scan = scan_literal_lines(content, "retry", context_lines=2, max_matches=10)
    assert scan.windows == []
    assert scan.match_count == 0
    assert scan.next_match_line is None


def test_numbered_read_preserves_blank_lines_and_bounds_continuation() -> None:
    text = format_line_read(
        "alpha\n", start_line=4, end_line=5, total_lines=6, includes_frontmatter=True
    )
    assert "4: alpha\n5: " in text
    assert "next: start_line=6, end_line=6" in text


def test_an_empty_document_numbers_no_lines() -> None:
    text = format_line_read("", start_line=0, end_line=0, total_lines=0, includes_frontmatter=True)

    assert text == "Lines 0-0 of 0 (document, including frontmatter); EOF"


@pytest.mark.parametrize(("first", "last", "total"), [(1, 0, 0), (20, 10, 10)])
def test_numbered_empty_or_past_eof(first: int, last: int, total: int) -> None:
    assert format_line_read(
        "", start_line=first, end_line=last, total_lines=total, includes_frontmatter=True
    ).endswith("; EOF")


def test_text_file_header_names_only_the_range() -> None:
    """A plain text file has no frontmatter, so the header does not claim it (#1976)."""
    text = format_line_read(
        "ten\neleven\ntwelve",
        start_line=10,
        end_line=12,
        total_lines=30,
        includes_frontmatter=False,
    )

    assert text.startswith("Lines 10-12 of 30; next: start_line=13, end_line=15")
    assert "frontmatter" not in text
