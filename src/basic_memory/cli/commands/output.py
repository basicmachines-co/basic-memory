"""Output modes and renderers for the shell-native read commands.

`bm cat`, `bm grep` and `bm inspect` print in one of three modes:

- **JSON**: raw machine-readable JSON, with ``--json`` or whenever stdout is not a
  terminal, so scripts stay parseable.
- **Rich**: colored panels and tables, the default in a terminal.
- **Plain**: undecorated, greppable text with ``--plain``, even when piped.

Precedence, highest first: ``--json`` > ``--plain`` > non-terminal (JSON) > terminal
(config ``cli_output_style``, ``rich`` by default). ``--json`` with ``--plain`` is an
error.
"""

import json
import sys
from typing import Any, Literal

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as markup_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from basic_memory.config import ConfigManager
from basic_memory.file_utils import has_frontmatter, remove_frontmatter

# Shared Rich console (stderr=False so output goes to stdout, matching print_json).
console = Console()


# --- Shared helpers ---

OutputMode = Literal["json", "rich", "plain"]


def stdout_is_terminal() -> bool:
    """Return True when stdout is an interactive TTY.

    Why: piped output (scripts, jq, etc.) must stay machine-parseable; the
         interactive (Rich/plain) renderers are only the default in a terminal.
    Outcome: a formatted renderer in a terminal; raw JSON when piped or redirected.

    Note: tests patch this to simulate a TTY, so the precedence logic in
    ``resolve_output_mode`` routes its terminal check through here.
    """
    return sys.stdout.isatty()


def validate_output_flags(json_output: bool, plain: bool) -> None:
    """Reject the contradictory --json/--plain combination.

    Trigger: both --json and --plain were passed.
    Why: they request mutually exclusive output modes (raw JSON vs undecorated
         human text); silently picking one would hide a user mistake.
    Outcome: a clear typer error with a non-zero exit.
    """
    if json_output and plain:
        typer.echo("Error: --json and --plain are mutually exclusive.", err=True)
        raise typer.Exit(1)


def resolve_output_mode(json_output: bool, plain: bool) -> OutputMode:
    """Resolve the effective output mode from flags, TTY state, and config.

    Precedence, highest first:
      1. --json            → raw JSON (wins over everything else)
      2. --plain           → undecorated plain text (even when piped)
      3. non-TTY stdout    → raw JSON (script compatibility, unchanged)
      4. TTY               → config ``cli_output_style`` (rich by default)

    Callers must invoke ``validate_output_flags`` first; this helper assumes the
    --json/--plain combination has already been rejected.
    """
    if json_output:
        return "json"
    if plain:
        return "plain"
    if not stdout_is_terminal():
        return "json"
    # Trigger: interactive TTY with no explicit mode flag.
    # Why: let users choose their default terminal experience without a flag.
    # Outcome: honor cli_output_style (rich out of the box, plain if configured).
    return ConfigManager().config.cli_output_style


def print_json(result: Any) -> None:
    """Print a result as formatted JSON."""
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))


def search_result_summary(result: dict[str, Any]) -> str:
    """Describe search count and pagination without inventing a final page."""
    results = result.get("results", [])
    raw_total = result.get("total")
    raw_total_is_exact = result.get("total_is_exact")
    if isinstance(raw_total_is_exact, bool):
        total_is_known = raw_total_is_exact and isinstance(raw_total, int)
    else:
        # Older payloads used a positive total for exact counts and zero as the
        # unknown-total sentinel, so retain that fallback during compatibility.
        total_is_known = isinstance(raw_total, int) and raw_total > 0
    total = raw_total if total_is_known else len(results)
    page = result.get("current_page") or result.get("page", 1)
    page_size = result.get("page_size", len(results)) or 1

    summary = f"{total} result(s)  •  page {page}"
    if total_is_known:
        summary += f" of {max(1, -(-total // page_size))}"
    if result.get("has_more") is True:
        summary += "  •  more results available"
    return summary


# --- Rich formatters ---


def display_search_results(result: dict[str, Any], query: str = "") -> None:
    """Render search-notes results as a Rich table.

    Real SearchResponse.model_dump() shape:
      results: list of SearchResult dicts (title, type, permalink, score, matched_chunk, content)
      current_page: int   (NOT "page")
      page_size: int
      total: int
      total_is_exact: bool
      has_more: bool
    """
    results = result.get("results", [])
    # Trigger: query is user-supplied text that may contain Rich markup characters.
    # Why: interpolating it directly into a markup string causes brackets to be
    #      parsed as style tags, swallowing or restyling bracketed content.
    # Outcome: escape the query so its literal characters are always displayed.
    escaped_query = markup_escape(query) if query else query
    title = f"Search: [bold cyan]{escaped_query}[/bold cyan]" if query else "Search results"
    subtitle = search_result_summary(result)

    if not results:
        console.print(Panel(Text("No results found.", style="dim"), title=title, expand=False))
        return

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Type", style="dim", width=12)
    table.add_column("Title", style="bold cyan")
    table.add_column("Score", style="yellow", width=7)
    table.add_column("Permalink", style="green")
    table.add_column("Snippet", style="dim", max_width=60)

    for item in results:
        item_type = item.get("type", "")
        # Trigger: user-sourced title/permalink may contain bracketed text.
        # Why: Rich table cells with a style column interpret markup in cell values,
        #      swallowing brackets (e.g. "Spec [draft] v2" → "Spec  v2").
        # Outcome: escape every user-sourced cell value before adding to the table.
        item_title = markup_escape(item.get("title") or item.get("permalink", ""))
        permalink = markup_escape(item.get("permalink", ""))
        score = item.get("score")
        score_str = f"{score:.2f}" if score is not None else ""
        # Prefer matched_chunk as the most relevant snippet; fall back to content.
        raw_snippet = item.get("matched_chunk") or item.get("content") or ""
        # Truncate to ~200 chars so the table stays readable.
        snippet = markup_escape(raw_snippet[:200].replace("\n", " ")) if raw_snippet else ""
        table.add_row(item_type, item_title, score_str, permalink, snippet)

    console.print(Panel(table, title=title, subtitle=subtitle, expand=False))


def display_read_note(result: dict[str, Any], *, include_frontmatter: bool = False) -> None:
    """Render read-note result: header panel + optional frontmatter + rendered Markdown content."""
    title = str(result.get("title") or "")
    permalink = str(result.get("permalink") or "")
    raw_content = result.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    raw_frontmatter = result.get("frontmatter")
    frontmatter: dict[str, Any] = raw_frontmatter if isinstance(raw_frontmatter, dict) else {}

    # Trigger: read_note returns an explicit all-None payload when no note resolves.
    # Why: treating those values as strings crashes Rich and hides any related-note
    #      suggestions that the MCP fallback already found.
    # Outcome: render a stable not-found panel, including safe, copyable suggestions.
    if raw_content is None and not title and not permalink:
        raw_related = result.get("related_results")
        related_results: list[dict[str, Any]] = (
            [item for item in raw_related if isinstance(item, dict)]
            if isinstance(raw_related, list)
            else []
        )
        if related_results:
            table = Table(show_header=True, header_style="bold", expand=False)
            table.add_column("Title", style="bold cyan")
            table.add_column("Permalink", style="green")
            table.add_column("File", style="dim")
            for item in related_results:
                table.add_row(
                    markup_escape(str(item.get("title") or "")),
                    markup_escape(str(item.get("permalink") or "")),
                    markup_escape(str(item.get("file_path") or "")),
                )
            console.print(
                Panel(
                    table,
                    title="Note not found",
                    subtitle=f"{len(related_results)} related result(s)",
                    expand=False,
                )
            )
        else:
            message = str(result.get("error") or "No note or related content found.")
            console.print(Panel(Text(message, style="dim"), title="Note not found", expand=False))
        return

    # The header already uses Text.append so title is never markup-interpreted.
    header = Text()
    header.append(title, style="bold cyan")
    if permalink:
        header.append(f"  [{permalink}]", style="dim green")

    console.print(Panel(header, expand=False))

    # Trigger: --frontmatter was passed; the MCP tool populates "frontmatter".
    # Why: the JSON payload always carries a "frontmatter" key regardless of the flag,
    #      so checking non-empty alone would render it even without the flag.  The flag
    #      must be threaded in to gate the panel.
    # Outcome: print a dim key/value block above the content only when the flag is set.
    if include_frontmatter and frontmatter:
        fm_table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
        fm_table.add_column("key", style="dim")
        fm_table.add_column("value", style="dim")
        for key, value in frontmatter.items():
            # Trigger: frontmatter keys/values are user-sourced and may contain markup.
            # Why: Rich table cells with a style column parse markup, so bracketed
            #      keys or values would be silently consumed or restyled.
            # Outcome: escape both key and value before adding them to the table.
            fm_table.add_row(markup_escape(str(key)), markup_escape(str(value)))
        console.print(Panel(fm_table, title="[dim]frontmatter[/dim]", expand=False))

    # Trigger: --frontmatter makes the API return the literal file, so
    # content starts with the frontmatter block the panel above already shows.
    # Why: rendering it again through Markdown duplicates the frontmatter (and
    #      Markdown mangles the --- fences into rules/headings).
    # Outcome: strip the block from the body; the panel is the frontmatter view.
    body = content
    if include_frontmatter and content and has_frontmatter(content):
        body = remove_frontmatter(content)
    if body and body.strip():
        console.print(Markdown(body))
    else:
        console.print(Text("(no content)", style="dim"))


# --- Plain formatters ---
#
# Plain output is NOT Rich markup: it is undecorated, greppable text printed via
# the builtin print().  Literal brackets ([draft], [fact]) must survive verbatim,
# so we deliberately do NOT call rich.markup.escape here -- escaping is only for
# the Rich path and would corrupt literal brackets in plain text.


def plain_search_results(result: dict[str, Any], query: str = "") -> None:
    """Render search-notes results as numbered plain-text entries.

    Mirrors the Rich table content: a header line, then one numbered block per
    result (title / score / permalink) with an indented snippet line.
    """
    results = result.get("results", [])
    header = f"Search: {query}" if query else "Search results"
    print(header)
    print(search_result_summary(result).replace("  •  ", " | "))

    if not results:
        print("No results found.")
        return

    for index, item in enumerate(results, start=1):
        item_title = item.get("title") or item.get("permalink", "")
        permalink = item.get("permalink", "")
        score = item.get("score")
        score_str = f"{score:.2f}" if score is not None else ""
        print(f"{index}. {item_title}  (score: {score_str})  {permalink}")
        raw_snippet = item.get("matched_chunk") or item.get("content") or ""
        if raw_snippet:
            snippet = raw_snippet[:200].replace("\n", " ")
            print(f"    {snippet}")
