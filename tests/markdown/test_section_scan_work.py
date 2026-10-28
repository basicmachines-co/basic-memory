"""Bound section-boundary work independently of machine timing (#1890)."""

from typing import SupportsIndex, overload, override

import pytest

from basic_memory.markdown import sections


class CountedHeadings(list[sections._ScannedHeading]):
    accessed: int = 0

    @overload
    def __getitem__(self, key: SupportsIndex, /) -> sections._ScannedHeading: ...

    @overload
    def __getitem__(self, key: slice, /) -> list[sections._ScannedHeading]: ...

    @override
    def __getitem__(
        self, key: SupportsIndex | slice, /
    ) -> sections._ScannedHeading | list[sections._ScannedHeading]:
        result = super().__getitem__(key)
        # Charge copied references too: a suffix copy costs work even when its
        # consumer stops after the first heading.
        self.accessed += len(result) if isinstance(result, list) else 1
        return result


@pytest.mark.parametrize("levels", [(1,), (1, 2, 3, 4, 5, 6), (1, 6, 6, 3, 5, 2)])
def test_section_boundary_work_is_linear(monkeypatch: pytest.MonkeyPatch, levels: tuple[int, ...]):
    body = "".join(f"{'#' * level} Heading {i}\n" for i, level in enumerate(levels * 100))
    headings = CountedHeadings(sections._scan_headings(body))
    monkeypatch.setattr(sections, "_scan_headings", lambda _: headings)

    scanned = sections.scan_sections(body)

    assert len(scanned) == len(headings)
    assert headings.accessed <= 6 * len(headings)
    # Verify every boundary against the definition, independently of the scanner.
    for index, section in enumerate(scanned):
        expected_end = next(
            (
                later.start_line - 1
                for later in scanned[index + 1 :]
                if later.level <= section.level
            ),
            len(headings),
        )
        assert section.end_line == expected_end
        assert (
            body.encode()[section.start_offset : section.end_offset]
            .decode()
            .startswith(f"{'#' * section.level} {section.heading}\n")
        )
