"""Focused CommonMark section-boundary regressions for EntityService."""

import pytest

from basic_memory.services.entity_service import EntityService


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_replace_section_stops_at_indented_atx_heading(indent: str) -> None:
    """One-to-three leading spaces still form a CommonMark ATX heading."""
    service = EntityService.__new__(EntityService)
    current = f"## A\nold body\n{indent}## B\nkeep body"

    result = service.replace_section_content(current, "## A", "new body")

    assert result == f"## A\nnew body\n{indent}## B\nkeep body"


def test_replace_section_does_not_stop_at_four_space_indented_heading() -> None:
    """A four-space-indented heading is code, so the section consumes it."""
    service = EntityService.__new__(EntityService)
    current = "## A\nold body\n    ## code heading\nstill old body\n## B\nkeep body"

    result = service.replace_section_content(current, "## A", "new body")

    assert result == "## A\nnew body\n## B\nkeep body"
