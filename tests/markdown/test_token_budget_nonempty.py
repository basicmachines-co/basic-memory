"""A small read budget must not return only leading whitespace (#1843)."""

import pytest

from basic_memory.markdown.sections import LineRange, NoteSlice, slice_note_content


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
@pytest.mark.parametrize("explicit_lines", [False, True])
def test_tiny_budget_keeps_the_first_nonblank_line(newline, explicit_lines):
    first_line = "An opening paragraph too long for the budget."
    document = newline.join(["---", "title: Plan", "---", "", " ", first_line, "", "Tail"])
    result = slice_note_content(
        document, max_tokens=1, lines=LineRange(4, None) if explicit_lines else None
    )

    assert isinstance(result, NoteSlice)
    assert first_line in result.content
    assert result.start_line == 4
    assert result.end_line == 6
    assert result.continue_line == 7
    following = slice_note_content(document, max_tokens=1, lines=LineRange(7, None))
    assert isinstance(following, NoteSlice)
    assert following.content.lstrip() == "Tail"
    assert following.continue_line is None


def test_whitespace_only_body_does_not_create_empty_continuation_pages():
    result = slice_note_content("---\ntitle: Blank\n---\n\n \n \n", max_tokens=1)

    assert isinstance(result, NoteSlice)
    assert result.content.strip() == ""
    assert result.continue_line is None
    assert result.truncated is False
