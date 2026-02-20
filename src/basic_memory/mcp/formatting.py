"""Formatting helpers for MCP tool outputs."""

from __future__ import annotations

from typing import Sequence

from basic_memory.schemas.search import SearchResponse, SearchResult

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_CYAN = "\x1b[36m"


def _apply_style(text: str, style: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{style}{text}{ANSI_RESET}"


def _strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text

    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :]).lstrip()
    return text


def _parse_title(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _make_separator(widths: Sequence[int]) -> str:
    return "+" + "+".join("-" * (width + 2) for width in widths) + "+"


def _format_row(values: Sequence[str], widths: Sequence[int]) -> str:
    cells = []
    for value, width in zip(values, widths, strict=True):
        cells.append(f" {_truncate(value, width).ljust(width)} ")
    return "|" + "|".join(cells) + "|"


def _get_result_tags(result: SearchResult) -> str:
    metadata = result.metadata or {}
    if isinstance(metadata, dict):
        tags = metadata.get("tags")
        if isinstance(tags, list):
            return ", ".join(str(tag) for tag in tags if tag)
    return ""


def _get_result_path(result: SearchResult) -> str:
    return result.permalink or result.file_path or ""


def format_search_results_ascii(
    result: SearchResponse,
    query: str | None = None,
    color: bool = False,
) -> str:
    """Format search results as an ASCII table for TUI clients."""

    results = result.results or []
    header_line = _apply_style("Search results", f"{ANSI_BOLD}{ANSI_CYAN}", color)
    lines = [header_line]

    if query:
        lines.append(f"Query: {query}")

    summary = (
        f"Results: {len(results)} | Page: {result.current_page} | Page size: {result.page_size}"
    )
    lines.append(_apply_style(summary, ANSI_DIM, color))

    if not results:
        lines.append("No results.")
        return "\n".join(lines).strip()

    headers = ["#", "Title", "Type", "Score", "Path", "Tags"]
    rows = []
    for idx, item in enumerate(results, start=1):
        rows.append(
            [
                str(idx),
                item.title or "Untitled",
                item.type.value if hasattr(item.type, "value") else str(item.type),
                f"{item.score:.2f}" if isinstance(item.score, (int, float)) else "",
                _get_result_path(item),
                _get_result_tags(item),
            ]
        )

    max_widths = [3, 32, 10, 7, 36, 24]
    widths = []
    for index, header in enumerate(headers):
        column_values = [header] + [row[index] for row in rows]
        max_len = max(len(value) for value in column_values)
        widths.append(min(max_widths[index], max_len))

    table = [_make_separator(widths)]
    header_row = _format_row(headers, widths)
    if color:
        header_cells = []
        for value, width in zip(headers, widths, strict=True):
            padded = f" {_truncate(value, width).ljust(width)} "
            header_cells.append(_apply_style(padded, f"{ANSI_BOLD}{ANSI_CYAN}", color))
        header_row = "|" + "|".join(header_cells) + "|"
    table.append(header_row)
    table.append(_make_separator(widths))

    for row in rows:
        table.append(_format_row(row, widths))

    table.append(_make_separator(widths))

    lines.append("")
    lines.extend(table)
    return "\n".join(lines).rstrip()


def format_note_preview_ascii(
    content: str,
    identifier: str | None = None,
    color: bool = False,
) -> str:
    """Format note content for ASCII/TUI display."""

    identifier = identifier or ""
    cleaned = _strip_frontmatter(content)
    title = _parse_title(cleaned) or identifier or "Note Preview"

    header = _apply_style("Note preview", f"{ANSI_BOLD}{ANSI_CYAN}", color)
    lines = [header, f"Title: {title}"]

    if identifier:
        lines.append(f"Identifier: {identifier}")

    lines.append(_apply_style("-" * 72, ANSI_DIM, color))

    if content.strip():
        lines.append(content.rstrip())
    else:
        lines.append("(empty note)")

    return "\n".join(lines).rstrip()
