"""Bounded literal-match windows over the same document lines as note slices."""

from dataclasses import dataclass

from basic_memory.markdown.sections import document_lines


@dataclass(frozen=True)
class MatchWindow:
    start_line: int
    end_line: int
    match_lines: list[int]
    content: str


@dataclass(frozen=True)
class LiteralLineScan:
    total_lines: int
    match_count: int
    windows: list[MatchWindow]
    next_match_line: int | None


def scan_literal_lines(
    content: str, pattern: str, *, context_lines: int, max_matches: int
) -> LiteralLineScan:
    """Find case-insensitive literal substrings; merge overlapping context windows.

    Count all matching lines, but return context for only the first max_matches.
    The next omitted match points to a useful follow-up read without copying the
    rest of the note into the response. Inputs are validated by the tool boundary.
    """
    lines = document_lines(content)
    needle = pattern.casefold()
    matches = [number for number, line in enumerate(lines, 1) if needle in line.casefold()]
    ranges: list[tuple[int, int, list[int]]] = []
    for number in matches[:max_matches]:
        first = max(1, number - context_lines)
        last = min(len(lines), number + context_lines)
        if ranges and first <= ranges[-1][1] + 1:
            previous_first, previous_last, previous_matches = ranges.pop()
            ranges.append((previous_first, max(previous_last, last), [*previous_matches, number]))
        else:
            ranges.append((first, last, [number]))
    return LiteralLineScan(
        total_lines=len(lines),
        match_count=len(matches),
        windows=[
            MatchWindow(first, last, numbers, "\n".join(lines[first - 1 : last]))
            for first, last, numbers in ranges
        ],
        next_match_line=matches[max_matches] if len(matches) > max_matches else None,
    )


def format_line_read(
    content: str,
    *,
    start_line: int,
    end_line: int,
    total_lines: int,
) -> str:
    """Render a slice with copyable coordinates and a bounded continuation hint."""
    header = f"Lines {start_line}-{end_line} of {total_lines} (document, including frontmatter)"
    # The slice has no terminal newline. Splitting on '\n' preserves a final
    # blank selected line; the coordinate range distinguishes it from EOF.
    numbered = (
        "\n".join(
            f"{number}: {line}"
            for number, line in zip(
                range(start_line, end_line + 1), content.split("\n"), strict=True
            )
        )
        if end_line >= start_line
        else ""
    )
    if end_line < total_lines:
        width = end_line - start_line + 1
        header += (
            f"; next: start_line={end_line + 1}, end_line={min(total_lines, end_line + width)}"
        )
    else:
        header += "; EOF"
    return f"{header}\n{numbered}" if numbered else header
