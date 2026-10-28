"""Whitespace-heavy tokens must not repeatedly scan failed suffixes (#1900)."""

import subprocess
import sys

import pytest

from basic_memory.markdown.plugins import remove_links_to_directive


def test_whitespace_token_completes_with_bounded_work():
    # Isolate the real parser calls so a regressed regex cannot hang pytest.
    script = """
from basic_memory.markdown.plugins import (
    remove_links_to_directive, _remove_links_to_directive_outside_inline_code,
)
content = '[[Target]]' + ' ' * 150000 + 'x'
assert remove_links_to_directive(content) == (content, False)
assert _remove_links_to_directive_outside_inline_code(content, []) == (content, False)
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=5, capture_output=True)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("text #bm:links_to", ("text", True)),
        ("text\t\u2003#bm:links_to\n", ("text", True)),
        (" #bm:links_to", ("", True)),
        ("#bm:links_to", ("#bm:links_to", False)),
        ("text#bm:links_to", ("text#bm:links_to", False)),
        ("text #bm:links_to extra", ("text #bm:links_to extra", False)),
    ],
)
def test_terminal_directive_semantics(content: str, expected: tuple[str, bool]):
    assert remove_links_to_directive(content) == expected
