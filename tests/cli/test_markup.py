"""User text interpolated into Rich markup prints verbatim (#1720)."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from basic_memory.cli.markup import literal

# Each value is one way Rich used to misread user text inside a markup string:
# a bracketed word it dropped, a closing tag that raised MarkupError, and a tag
# that restyled the line.
BRACKETED_VALUES = ["proj [x]", "a[/b]c", "[bold]x[/bold]"]


def _render(markup: str) -> str:
    output = io.StringIO()
    Console(file=output, no_color=True, width=200).print(markup)
    return output.getvalue().rstrip("\n")


@pytest.mark.parametrize("value", BRACKETED_VALUES)
def test_literal_text_prints_verbatim_inside_markup(value):
    assert _render(f"[red]Error for '{literal(value)}'[/red]") == f"Error for '{value}'"


@pytest.mark.parametrize(
    "value",
    [Path("/notes/[draft]/a.md"), ValueError("bad [x] value"), 42],
    ids=["path", "exception", "int"],
)
def test_literal_accepts_non_string_values(value):
    """Paths, exceptions, and numbers are printed too; rich's escape only takes str."""
    assert _render(f"[red]{literal(value)}[/red]") == str(value)
