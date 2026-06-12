"""CLI tool commands for Basic Memory.

Every command calls its MCP tool with output_format="json" and prints the result.
Commands that benefit from human-readable output (search-notes, read-note,
build-context, recent-activity) support three output modes:

- **JSON** — raw machine-readable JSON.  Used when ``--json`` is passed, or
  automatically when stdout is not a TTY (piped/redirected), so scripts stay
  parseable.  This follows the same bm status / bm project list precedent.
- **Rich** — colored Panel/Table/Tree/Markdown output.  The default interactive
  experience when stdout is a TTY.
- **Plain** — undecorated, greppable text (no ANSI colors, no box-drawing, no
  markup).  Forced with ``--plain`` even when piped.

Precedence, highest first: ``--json`` > ``--plain`` > non-TTY (JSON) > TTY
(config ``cli_output_style``, ``rich`` by default).  Passing both ``--json`` and
``--plain`` is an error.  The interactive default for a TTY is controlled by the
``cli_output_style`` config option (``rich``/``plain``; env
``BASIC_MEMORY_CLI_OUTPUT_STYLE``).
"""

import json
import sys
from typing import Annotated, Any, Dict, List, Literal, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as markup_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags

# MCP tool functions are imported inside each command: importing
# basic_memory.mcp.tools loads the entire tool stack (fastmcp, mcp SDK,
# SQLAlchemy), which would slow every CLI invocation, including --help (#886).

tool_app = typer.Typer()
app.add_typer(tool_app, name="tool", help="Access to MCP tools via CLI")

VALID_EDIT_OPERATIONS = ["append", "prepend", "find_replace", "replace_section"]

# Shared Rich console (stderr=False so output goes to stdout, matching _print_json).
console = Console()


# --- Shared helpers ---

OutputMode = Literal["json", "rich", "plain"]


def _use_rich() -> bool:
    """Return True when stdout is an interactive TTY.

    Why: piped output (scripts, jq, etc.) must stay machine-parseable; the
         interactive (Rich/plain) renderers are only the default in a terminal.
    Outcome: a formatted renderer in a terminal; raw JSON when piped or redirected.

    Note: tests patch this to simulate a TTY, so the precedence logic in
    ``_resolve_output_mode`` routes its terminal check through here.
    """
    return sys.stdout.isatty()


def _validate_output_flags(json_output: bool, plain: bool) -> None:
    """Reject the contradictory --json/--plain combination.

    Trigger: both --json and --plain were passed.
    Why: they request mutually exclusive output modes (raw JSON vs undecorated
         human text); silently picking one would hide a user mistake.
    Outcome: a clear typer error with a non-zero exit.
    """
    if json_output and plain:
        typer.echo("Error: --json and --plain are mutually exclusive.", err=True)
        raise typer.Exit(1)


def _resolve_output_mode(json_output: bool, plain: bool) -> OutputMode:
    """Resolve the effective output mode from flags, TTY state, and config.

    Precedence, highest first:
      1. --json            → raw JSON (wins over everything else)
      2. --plain           → undecorated plain text (even when piped)
      3. non-TTY stdout    → raw JSON (script compatibility, unchanged)
      4. TTY               → config ``cli_output_style`` (rich by default)

    Callers must invoke ``_validate_output_flags`` first; this helper assumes the
    --json/--plain combination has already been rejected.
    """
    if json_output:
        return "json"
    if plain:
        return "plain"
    if not _use_rich():
        return "json"
    # Trigger: interactive TTY with no explicit mode flag.
    # Why: let users choose their default terminal experience without a flag.
    # Outcome: honor cli_output_style (rich out of the box, plain if configured).
    return ConfigManager().config.cli_output_style


def _print_json(result: Any) -> None:
    """Print a result as formatted JSON."""
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))


# --- Rich formatters ---


def _display_search_results(result: dict[str, Any], query: str = "") -> None:
    """Render search-notes results as a Rich table.

    Real SearchResponse.model_dump() shape:
      results: list of SearchResult dicts (title, type, permalink, score, matched_chunk, content)
      current_page: int   (NOT "page")
      page_size: int
      total: int
      has_more: bool
    """
    results = result.get("results", [])
    # Trigger: API returns total=0 even when results is non-empty (upstream quirk).
    # Why: the key exists with value 0, so result.get("total", len(results)) never
    #      applies its default, leaving the subtitle as "0 result(s)" under a
    #      populated table.
    # Outcome: fall back to len(results) when total is falsy but results is non-empty.
    raw_total = result.get("total", len(results))
    total = raw_total if raw_total else len(results)
    # Real key is "current_page"; fall back to "page" for forward-compat.
    page = result.get("current_page") or result.get("page", 1)
    page_size = result.get("page_size", len(results)) or 1

    # Trigger: query is user-supplied text that may contain Rich markup characters.
    # Why: interpolating it directly into a markup string causes brackets to be
    #      parsed as style tags, swallowing or restyling bracketed content.
    # Outcome: escape the query so its literal characters are always displayed.
    escaped_query = markup_escape(query) if query else query
    title = f"Search: [bold cyan]{escaped_query}[/bold cyan]" if query else "Search results"
    subtitle = f"{total} result(s)  •  page {page} of {max(1, -(-total // page_size))}"

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


def _display_read_note(result: dict[str, Any], *, include_frontmatter: bool = False) -> None:
    """Render read-note result: header panel + optional frontmatter + rendered Markdown content."""
    title = result.get("title", "")
    permalink = result.get("permalink", "")
    content = result.get("content", "")
    frontmatter: dict[str, Any] = result.get("frontmatter") or {}

    # The header already uses Text.append so title is never markup-interpreted.
    header = Text()
    header.append(title, style="bold cyan")
    if permalink:
        header.append(f"  [{permalink}]", style="dim green")

    console.print(Panel(header, expand=False))

    # Trigger: --include-frontmatter was passed; the MCP tool populates "frontmatter".
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

    if content:
        console.print(Markdown(content))
    else:
        console.print(Text("(no content)", style="dim"))


def _display_build_context(result: dict[str, Any]) -> None:
    """Render build-context result as a Rich tree.

    Real GraphContext.model_dump() shape:
      results: list of ContextResult dicts, each with:
        primary_result: EntitySummary | RelationSummary | ObservationSummary
        observations:   list of ObservationSummary
        related_results: list of EntitySummary | RelationSummary | ObservationSummary
      metadata: {"uri": ..., ...}
      page/page_size/has_more

    Each summary has: type, title (EntitySummary/RelationSummary), permalink,
    and relation_type (RelationSummary only).
    """
    metadata = result.get("metadata", {})
    uri = metadata.get("uri", "")
    context_items: list[dict[str, Any]] = list(result.get("results", []))

    # Trigger: uri is user-sourced and may contain Rich markup characters.
    # Why: interpolating it directly into a markup string causes brackets to be
    #      parsed as style tags, swallowing or restyling bracketed content.
    # Outcome: escape the uri so its literal characters are always displayed.
    label = f"[bold cyan]{markup_escape(uri)}[/bold cyan]" if uri else "Context"
    tree = Tree(f"[bold]Context:[/bold] {label}")

    if not context_items:
        tree.add("[dim]No related content found.[/dim]")
    else:
        for context_result in context_items:
            # --- Primary result node ---
            primary = context_result.get("primary_result", {})
            # Trigger: p_title and p_type are user-sourced values from the knowledge graph.
            # Why: embedding them in markup strings without escaping would cause any
            #      bracketed text (e.g. an entity titled "Spec [draft]") to be consumed
            #      by the Rich markup parser and silently dropped from output.
            # Outcome: escape all user values before interpolating into markup strings.
            p_title = markup_escape(primary.get("title") or primary.get("permalink", ""))
            p_type = markup_escape(primary.get("type", ""))
            primary_label = f"[cyan]{p_title}[/cyan]"
            if p_type:
                primary_label = f"[dim]{p_type}[/dim]  {primary_label}"
            primary_node = tree.add(primary_label)

            # --- Observations as children (category + truncated content) ---
            # Trigger: ContextResult.observations exists in the JSON output but was
            #          never rendered in the Rich path.
            # Why: users running interactively lost core entity facts (observations)
            #      that the --json path exposes; the TTY view must be at least as
            #      informative as the JSON view for the primary entity.
            # Outcome: each observation appears as a dim "[category] content" leaf
            #          under its primary node, truncated at 120 chars.
            observations: list[dict[str, Any]] = list(context_result.get("observations", []))
            for obs in observations:
                category = obs.get("category", "")
                obs_content = obs.get("content", "")
                # Truncate long observations so the tree stays readable.
                if len(obs_content) > 120:
                    obs_content = obs_content[:117] + "..."
                # Trigger: category and obs_content are user-sourced strings that may
                #          contain Rich markup characters.  The category is also wrapped
                #          in literal "[" "]" brackets in the label, which must be
                #          escaped too so Rich does not treat "[fact]" as a style tag.
                # Why: embedding "[fact]" in a markup string causes Rich to parse it as
                #      an unknown tag and silently drop the text.
                # Outcome: escape the full "[category] content" fragment including the
                #          surrounding brackets before embedding it in a styled label.
                obs_label = f"[dim]{markup_escape(f'[{category}] {obs_content}')}[/dim]"
                primary_node.add(obs_label)

            # --- Related items as children ---
            related: list[dict[str, Any]] = list(context_result.get("related_results", []))
            for rel_item in related:
                rel_title = markup_escape(rel_item.get("title") or rel_item.get("permalink", ""))
                rel_type = markup_escape(rel_item.get("type", ""))
                relation = markup_escape(rel_item.get("relation_type", ""))

                parts = []
                if relation:
                    parts.append(f"[yellow]{relation}[/yellow]")
                if rel_type:
                    parts.append(f"[dim]{rel_type}[/dim]")
                parts.append(f"[cyan]{rel_title}[/cyan]")
                primary_node.add(" ".join(parts))

    # Count total related items across all primary results.
    total_related = sum(len(cr.get("related_results", [])) for cr in context_items)
    total_observations = sum(len(cr.get("observations", [])) for cr in context_items)
    subtitle = f"{len(context_items)} primary  •  {total_observations} observations  •  {total_related} related"
    console.print(Panel(tree, subtitle=subtitle, expand=False))


def _display_recent_activity(result: list[dict[str, Any]]) -> None:
    """Render recent-activity results as a Rich table."""
    if not result:
        console.print(
            Panel(Text("No recent activity.", style="dim"), title="Recent Activity", expand=False)
        )
        return

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Type", style="dim", width=12)
    table.add_column("Title", style="bold cyan")
    table.add_column("Permalink", style="green")
    table.add_column("Updated", style="dim")

    for item in result:
        item_type = item.get("type", "")
        # Trigger: title, permalink, and timestamps are user-sourced strings from the
        #          knowledge graph and may contain Rich markup characters.
        # Why: Rich table cells with a style column parse markup in cell values, so
        #      bracketed content would be silently consumed or restyled.
        # Outcome: escape all user-sourced cell values before adding to the table.
        item_title = markup_escape(item.get("title") or item.get("permalink", ""))
        permalink = markup_escape(item.get("permalink", ""))
        updated = str(item.get("updated_at") or item.get("created_at") or "")
        table.add_row(item_type, item_title, permalink, updated)

    console.print(Panel(table, title="Recent Activity", expand=False))


# --- Plain formatters ---
#
# Plain output is NOT Rich markup: it is undecorated, greppable text printed via
# the builtin print().  Literal brackets ([draft], [fact]) must survive verbatim,
# so we deliberately do NOT call rich.markup.escape here -- escaping is only for
# the Rich path and would corrupt literal brackets in plain text.


def _plain_search_results(result: dict[str, Any], query: str = "") -> None:
    """Render search-notes results as numbered plain-text entries.

    Mirrors the Rich table content: a header line, then one numbered block per
    result (title / score / permalink) with an indented snippet line.
    """
    results = result.get("results", [])
    # Mirror the Rich path's total fix: the API can return total=0 with a
    # populated results list, so fall back to len(results) when total is falsy.
    raw_total = result.get("total", len(results))
    total = raw_total if raw_total else len(results)

    header = f"Search: {query}" if query else "Search results"
    print(header)
    print(f"{total} result(s)")

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


def _plain_read_note(result: dict[str, Any], *, include_frontmatter: bool = False) -> None:
    """Render read-note as a plain title/permalink header, optional frontmatter, then body."""
    title = result.get("title", "")
    permalink = result.get("permalink", "")
    content = result.get("content", "")
    frontmatter: dict[str, Any] = result.get("frontmatter") or {}

    header = f"{title}  [{permalink}]" if permalink else title
    print(header)

    # Trigger: --include-frontmatter was passed and the payload carries frontmatter.
    # Why: the JSON payload always includes a "frontmatter" key, so the flag (not
    #      mere presence) gates whether the key/value block is printed -- matching
    #      the Rich path's gating behavior.
    # Outcome: a blank line then "key: value" lines above the body.
    if include_frontmatter and frontmatter:
        print()
        for key, value in frontmatter.items():
            print(f"{key}: {value}")

    print()
    if content:
        print(content)
    else:
        print("(no content)")


def _plain_build_context(result: dict[str, Any]) -> None:
    """Render build-context as an ASCII-indented outline.

    Each primary result is a top-level line; its observations and related items
    are two-space indented beneath it, mirroring the Rich tree content.
    """
    metadata = result.get("metadata", {})
    uri = metadata.get("uri", "")
    context_items: list[dict[str, Any]] = list(result.get("results", []))

    print(f"Context: {uri}" if uri else "Context")

    if not context_items:
        print("No related content found.")
        return

    for context_result in context_items:
        primary = context_result.get("primary_result", {})
        p_title = primary.get("title") or primary.get("permalink", "")
        p_type = primary.get("type", "")
        primary_line = f"{p_type}  {p_title}" if p_type else p_title
        print(primary_line)

        observations: list[dict[str, Any]] = list(context_result.get("observations", []))
        for obs in observations:
            category = obs.get("category", "")
            obs_content = obs.get("content", "")
            if len(obs_content) > 120:
                obs_content = obs_content[:117] + "..."
            print(f"  [{category}] {obs_content}")

        related: list[dict[str, Any]] = list(context_result.get("related_results", []))
        for rel_item in related:
            rel_title = rel_item.get("title") or rel_item.get("permalink", "")
            rel_type = rel_item.get("type", "")
            relation = rel_item.get("relation_type", "")
            parts = [part for part in (relation, rel_type, rel_title) if part]
            print(f"  {'  '.join(parts)}")


def _plain_recent_activity(result: list[dict[str, Any]]) -> None:
    """Render recent-activity as plain "- title (type) permalink updated" lines."""
    if not result:
        print("No recent activity.")
        return

    print("Recent Activity")
    for item in result:
        item_type = item.get("type", "")
        item_title = item.get("title") or item.get("permalink", "")
        permalink = item.get("permalink", "")
        updated = str(item.get("updated_at") or item.get("created_at") or "")
        print(f"- {item_title} ({item_type}) {permalink} {updated}".rstrip())


def _delete_note_failure_message(result: dict[str, Any]) -> str | None:
    """Return the CLI failure message for delete-note JSON results, if any."""
    error = result.get("error")
    if error:
        return str(error)

    failed_deletes = result.get("failed_deletes")
    # Trigger: directory deletion can partially fail without raising from the service.
    # Why: cleanup scripts need a non-zero exit when files remain undeleted.
    # Outcome: the CLI fails even if older MCP JSON did not include an error field.
    if (
        result.get("is_directory") is True
        and isinstance(failed_deletes, int)
        and failed_deletes > 0
    ):
        return f"Directory delete incomplete: {failed_deletes} file(s) failed"

    return None


# --- Commands ---


@tool_app.command()
def write_note(
    title: Annotated[str, typer.Option(help="The title of the note")],
    folder: Annotated[str, typer.Option(help="The folder to create the note in")],
    content: Annotated[
        Optional[str],
        typer.Option(
            help="The content of the note. If not provided, content will be read from stdin."
        ),
    ] = None,
    tags: Annotated[
        Optional[List[str]], typer.Option(help="A list of tags to apply to the note")
    ] = None,
    note_type: Annotated[
        str,
        typer.Option(
            "--type",
            help=(
                "Note type stored in frontmatter (e.g. 'guide', 'report'). "
                "A 'type:' in the note's own content frontmatter takes precedence."
            ),
        ),
    ] = "note",
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to write to. If not provided, the default project will be used."
        ),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing note on conflict (matches MCP write_note overwrite=True)",
    ),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Create or update a markdown note. Content can be provided via --content or stdin.

    Examples:

    bm tool write-note --title "My Note" --folder "notes" --content "Note content"
    bm tool write-note --title "My Guide" --folder "notes" --content "..." --type guide
    echo "content" | bm tool write-note --title "My Note" --folder "notes"
    bm tool write-note --title "My Note" --folder "notes" --overwrite
    bm tool write-note --title "My Note" --folder "notes" --local
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import write_note as mcp_write_note

    try:
        validate_routing_flags(local, cloud)

        # If content is not provided, read from stdin
        if content is None:
            if not sys.stdin.isatty():
                content = sys.stdin.read()
            else:  # pragma: no cover
                typer.echo(
                    "No content provided. Please provide content via --content or by piping to stdin.",
                    err=True,
                )
                raise typer.Exit(1)

        if content is not None and not content.strip():
            typer.echo("Empty content provided. Please provide non-empty content.", err=True)
            raise typer.Exit(1)

        assert content is not None

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_write_note(
                    title=title,
                    content=content,
                    directory=folder,
                    project=project,
                    project_id=project_id,
                    tags=tags,
                    note_type=note_type,
                    overwrite=overwrite,
                    output_format="json",
                )
            )

        # MCP tool returns an error field on failure in JSON mode (e.g.
        # NOTE_ALREADY_EXISTS on a blocked overwrite, SECURITY_VALIDATION_ERROR).
        # Trigger: result carries a non-empty `error`.
        # Why: parity with delete-note/edit-note/search-notes so exit-code-driven
        #      scripts detect a failed/blocked write instead of seeing exit 0.
        # Outcome: print the error to stderr and exit non-zero.
        if isinstance(result, dict) and result.get("error"):
            typer.echo(f"Error: {result['error']}", err=True)
            _print_json(result)
            raise typer.Exit(1)

        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during write_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def read_note(
    identifier: str,
    include_frontmatter: bool = typer.Option(
        False, "--include-frontmatter", help="Include YAML frontmatter in output"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of formatted display"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output undecorated plain text (no colors/markup), even when piped"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Read a markdown note from the knowledge base.

    Three output modes: Rich formatted Markdown (default in a terminal), plain
    undecorated text (--plain), and raw JSON (--json, or automatically when
    piped). The interactive default is set by the cli_output_style config option
    (rich/plain). --json and --plain are mutually exclusive.

    Examples:

    bm tool read-note my-note
    bm tool read-note my-note --include-frontmatter
    bm tool read-note my-note --plain
    bm tool read-note my-note --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import read_note as mcp_read_note

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_read_note(
                    identifier=identifier,
                    project=project,
                    project_id=project_id,
                    include_frontmatter=include_frontmatter,
                    output_format="json",
                )
            )

        # MCP tool returns an error field on failure in JSON mode (e.g.
        # SECURITY_VALIDATION_ERROR on a path-traversal identifier). A genuine
        # not-found returns null fields with no `error` key, so it still exits 0.
        # Trigger: result carries a non-empty `error`.
        # Why: parity with edit-note/delete-note/search-notes so a blocked read
        #      surfaces a non-zero exit instead of looking like success.
        # Outcome: print the error to stderr and exit non-zero.
        if isinstance(result, dict) and result.get("error"):
            typer.echo(f"Error: {result['error']}", err=True)
            _print_json(result)
            raise typer.Exit(1)

        # A string result (e.g. a not-found message) has no structured shape to
        # format, so always fall back to JSON regardless of the resolved mode.
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json" or isinstance(result, str):
            _print_json(result)
        elif mode == "plain":
            _plain_read_note(result, include_frontmatter=include_frontmatter)
        else:
            _display_read_note(result, include_frontmatter=include_frontmatter)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during read_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("delete-note")
def delete_note(
    identifier: str,
    is_directory: bool = typer.Option(
        False, "--is-directory", help="Delete a directory instead of a single note"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Delete a note or directory from the knowledge base.

    Examples:

    bm tool delete-note notes/old-draft
    bm tool delete-note docs/archive --is-directory
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import delete_note as mcp_delete_note

    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_delete_note(
                    identifier=identifier,
                    is_directory=is_directory,
                    project=project,
                    project_id=project_id,
                    output_format="json",
                )
            )

        if isinstance(result, dict):
            failure_message = _delete_note_failure_message(result)
            if failure_message:
                typer.echo(f"Error: {failure_message}", err=True)
                raise typer.Exit(1)

        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during delete_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def edit_note(
    identifier: str,
    operation: Annotated[str, typer.Option("--operation", help="Edit operation to apply")],
    content: Annotated[str, typer.Option("--content", help="Content for the edit operation")],
    find_text: Annotated[
        Optional[str], typer.Option("--find-text", help="Text to find for find_replace operation")
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option("--section", help="Section heading for replace_section operation"),
    ] = None,
    expected_replacements: int = typer.Option(
        1,
        "--expected-replacements",
        help="Expected replacement count for find_replace operation",
    ),
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to edit. If not provided, the default project will be used."
        ),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Edit an existing markdown note using append/prepend/find_replace/replace_section.

    Examples:

    bm tool edit-note my-note --operation append --content "new content"
    bm tool edit-note my-note --operation find_replace --find-text "old" --content "new"
    bm tool edit-note my-note --operation replace_section --section "## Notes" --content "updated"
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import edit_note as mcp_edit_note

    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_edit_note(
                    identifier=identifier,
                    operation=operation,
                    content=content,
                    project=project,
                    project_id=project_id,
                    section=section,
                    find_text=find_text,
                    expected_replacements=expected_replacements,
                    output_format="json",
                )
            )

        # MCP tool returns error field on failure in JSON mode
        if isinstance(result, dict) and result.get("error"):
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)

        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during edit_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def build_context(
    url: str,
    depth: Optional[int] = typer.Option(1, "--depth", help="Depth of context to build"),
    timeframe: Optional[str] = typer.Option(
        "7d", "--timeframe", help="Timeframe filter (e.g., '7d', '1 week')"
    ),
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    max_related: int = typer.Option(10, "--max-related", help="Maximum related items to return"),
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of formatted display"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output undecorated plain text (no colors/markup), even when piped"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get context needed to continue a discussion.

    Three output modes: a Rich tree view (default in a terminal), a plain
    ASCII-indented outline (--plain), and raw JSON (--json, or automatically when
    piped). The interactive default is set by the cli_output_style config option
    (rich/plain). --json and --plain are mutually exclusive.

    Examples:

    bm tool build-context memory://specs/search
    bm tool build-context specs/search --depth 2 --timeframe 30d
    bm tool build-context memory://specs/search --plain
    bm tool build-context memory://specs/search --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import build_context as mcp_build_context

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_build_context(
                    url=url,
                    project=project,
                    project_id=project_id,
                    depth=depth,
                    timeframe=timeframe,
                    page=page,
                    page_size=page_size,
                    max_related=max_related,
                    output_format="json",
                )
            )

        # A string result has no structured shape to format, so fall back to JSON.
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json" or isinstance(result, str):
            _print_json(result)
        elif mode == "plain":
            _plain_build_context(result)
        else:
            _display_build_context(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during build_context: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def recent_activity(
    type: Annotated[Optional[List[str]], typer.Option(help="Filter by item type")] = None,
    depth: Optional[int] = typer.Option(1, "--depth", help="Depth of context to build"),
    timeframe: Optional[str] = typer.Option(
        "7d", "--timeframe", help="Timeframe filter (e.g., '7d', '1 week')"
    ),
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    # Match the MCP recent_activity default (page_size=10) so identical default
    # invocations return the same number of rows from CLI and MCP.
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of formatted display"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output undecorated plain text (no colors/markup), even when piped"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get recent activity across the knowledge base.

    Three output modes: a formatted Rich table (default in a terminal), plain
    undecorated lines (--plain), and raw JSON (--json, or automatically when
    piped). The interactive default is set by the cli_output_style config option
    (rich/plain). --json and --plain are mutually exclusive.

    Examples:

    bm tool recent-activity
    bm tool recent-activity --timeframe 30d --page-size 20
    bm tool recent-activity --type entity --type observation
    bm tool recent-activity --plain
    bm tool recent-activity --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import recent_activity as mcp_recent_activity

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_recent_activity(
                    type=type or "",
                    depth=depth if depth is not None else 1,
                    timeframe=timeframe if timeframe is not None else "7d",
                    page=page,
                    page_size=page_size,
                    project=project,
                    project_id=project_id,
                    output_format="json",
                )
            )

        # A string result has no structured shape to format, so fall back to JSON.
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json" or isinstance(result, str):
            _print_json(result)
        elif mode == "plain":
            _plain_recent_activity(result)
        else:
            _display_recent_activity(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during recent_activity: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("search-notes")
def search_notes(
    query: Annotated[
        Optional[str],
        typer.Argument(help="Search query string (optional when using metadata filters)"),
    ] = "",
    permalink: Annotated[bool, typer.Option("--permalink", help="Search permalink values")] = False,
    title: Annotated[bool, typer.Option("--title", help="Search title values")] = False,
    vector: Annotated[bool, typer.Option("--vector", help="Use vector retrieval")] = False,
    hybrid: Annotated[bool, typer.Option("--hybrid", help="Use hybrid retrieval")] = False,
    after_date: Annotated[
        Optional[str],
        typer.Option("--after_date", help="Search results after date, eg. '2d', '1 week'"),
    ] = None,
    tags: Annotated[
        Optional[List[str]],
        typer.Option("--tag", help="Filter by frontmatter tag (repeatable)"),
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option("--status", help="Filter by frontmatter status"),
    ] = None,
    note_types: Annotated[
        Optional[List[str]],
        typer.Option("--type", help="Filter by frontmatter type (repeatable)"),
    ] = None,
    entity_types: Annotated[
        Optional[List[str]],
        typer.Option(
            "--entity-type",
            help="Filter by search item type: entity, observation, relation (repeatable)",
        ),
    ] = None,
    categories: Annotated[
        Optional[List[str]],
        typer.Option(
            "--category",
            help=(
                "Filter observation results to exact categories (repeatable); "
                "pair with --entity-type observation"
            ),
        ),
    ] = None,
    meta: Annotated[
        Optional[List[str]],
        typer.Option("--meta", help="Filter by frontmatter key=value (repeatable)"),
    ] = None,
    filter_json: Annotated[
        Optional[str],
        typer.Option("--filter", help="JSON metadata filter (advanced)"),
    ] = None,
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of formatted display"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output undecorated plain text (no colors/markup), even when piped"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Search across all content in the knowledge base.

    Three output modes: a formatted Rich table (default in a terminal), plain
    numbered text results (--plain), and raw JSON (--json, or automatically when
    piped). The interactive default is set by the cli_output_style config option
    (rich/plain). --json and --plain are mutually exclusive.

    Examples:

    bm tool search-notes "my query"
    bm tool search-notes --permalink "specs/*"
    bm tool search-notes --tag python --tag async
    bm tool search-notes --meta status=draft
    bm tool search-notes "auth" --entity-type observation --category requirement
    bm tool search-notes "my query" --plain
    bm tool search-notes "my query" --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import search_notes as mcp_search

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        mode_flags = [permalink, title, vector, hybrid]
        if sum(1 for enabled in mode_flags if enabled) > 1:  # pragma: no cover
            typer.echo(
                "Use only one mode flag: --permalink, --title, --vector, or --hybrid. Exiting.",
                err=True,
            )
            raise typer.Exit(1)

        # --- Build metadata filters from --filter and --meta ---
        metadata_filters: Dict[str, Any] | None = {}
        if filter_json:
            try:
                metadata_filters = json.loads(filter_json)
                if not isinstance(metadata_filters, dict):
                    raise ValueError("Metadata filter JSON must be an object")
            except json.JSONDecodeError as e:
                typer.echo(f"Invalid JSON for --filter: {e}", err=True)
                raise typer.Exit(1)

        if meta:
            for item in meta:
                if "=" not in item:
                    typer.echo(
                        f"Invalid --meta entry '{item}'. Use key=value format.",
                        err=True,
                    )
                    raise typer.Exit(1)
                key, value = item.split("=", 1)
                key = key.strip()
                if not key:
                    typer.echo(f"Invalid --meta entry '{item}'.", err=True)
                    raise typer.Exit(1)
                metadata_filters[key] = value

        if not metadata_filters:
            metadata_filters = None

        # --- Determine search type from mode flags ---
        search_type: str | None = None
        if permalink:
            search_type = "permalink"
        if title:
            search_type = "title"
        if vector:
            search_type = "vector"
        if hybrid:
            search_type = "hybrid"

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_search(
                    query=query or None,
                    project=project,
                    project_id=project_id,
                    search_type=search_type,
                    output_format="json",
                    page=page,
                    after_date=after_date,
                    page_size=page_size,
                    note_types=note_types,
                    entity_types=entity_types,
                    categories=categories,
                    metadata_filters=metadata_filters,
                    tags=tags,
                    status=status,
                )
            )

        # MCP tool may return a string error message
        if isinstance(result, str):
            typer.echo(result, err=True)
            raise typer.Exit(1)

        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            _print_json(result)
        elif mode == "plain":
            _plain_search_results(result, query=query or "")
        else:
            _display_search_results(result, query=query or "")
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            logger.exception("Error during search", e)
            typer.echo(f"Error during search: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- list-projects ---


@tool_app.command("list-projects")
def list_projects(
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """List all available projects with their status (JSON output).

    Examples:

    bm tool list-projects
    bm tool list-projects --local
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import list_memory_projects as mcp_list_projects

    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(mcp_list_projects(output_format="json"))
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during list_projects: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- list-workspaces ---


@tool_app.command("list-workspaces")
def list_workspaces(
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """List available cloud workspaces (JSON output).

    Examples:

    bm tool list-workspaces
    bm tool list-workspaces --cloud
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import list_workspaces as mcp_list_workspaces

    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(mcp_list_workspaces(output_format="json"))
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during list_workspaces: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- schema-validate ---


@tool_app.command("schema-validate")
def schema_validate(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Note path or note type to validate"),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Validate notes against their schemas (JSON output).

    TARGET can be a note path (e.g., people/ada-lovelace.md) or a note type
    (e.g., person). If omitted, validates all notes that have schemas.

    Examples:

    bm tool schema-validate person
    bm tool schema-validate people/ada-lovelace.md
    bm tool schema-validate --project research
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_validate as mcp_schema_validate

    try:
        validate_routing_flags(local, cloud)

        # Heuristic: if target contains / or ., treat as identifier; otherwise as note type
        note_type, identifier = None, None
        if target:
            if "/" in target or "." in target:
                identifier = target
            else:
                note_type = target

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_schema_validate(
                    note_type=note_type,
                    identifier=identifier,
                    project=project,
                    project_id=project_id,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during schema_validate: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- schema-infer ---


@tool_app.command("schema-infer")
def schema_infer(
    note_type: Annotated[
        str,
        typer.Argument(help="Note type to analyze (e.g., person, meeting)"),
    ],
    threshold: float = typer.Option(
        0.25, "--threshold", help="Minimum frequency for optional fields (0-1)"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Infer schema from existing notes of a type (JSON output).

    Examples:

    bm tool schema-infer person
    bm tool schema-infer meeting --threshold 0.5
    bm tool schema-infer person --project research
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_infer as mcp_schema_infer

    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_schema_infer(
                    note_type=note_type,
                    threshold=threshold,
                    project=project,
                    project_id=project_id,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during schema_infer: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- schema-diff ---


@tool_app.command("schema-diff")
def schema_diff(
    note_type: Annotated[
        str,
        typer.Argument(help="Note type to check for drift"),
    ],
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Show drift between schema and actual usage (JSON output).

    Examples:

    bm tool schema-diff person
    bm tool schema-diff person --project research
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_diff as mcp_schema_diff

    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_schema_diff(
                    note_type=note_type,
                    project=project,
                    project_id=project_id,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during schema_diff: {e}", err=True)
            raise typer.Exit(1)
        raise
