"""CLI tool commands for Basic Memory.

Every command calls its MCP tool with output_format="json" and prints the result.
Commands that benefit from human-readable output (search-notes, read-note,
build-context, recent-activity) default to Rich formatting when stdout is a TTY
and fall back to raw JSON when piped or when --json is supplied.  This follows
the same bm status / bm project list precedent.
"""

import json
import sys
from typing import Annotated, Any, Dict, List, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
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


def _use_rich() -> bool:
    """Return True when stdout is an interactive TTY and Rich output is appropriate.

    Trigger: caller did not pass --json and stdout is a TTY.
    Why: piped output (scripts, jq, etc.) must stay machine-parseable;
         human-readable formatting is only useful in an interactive terminal.
    Outcome: Rich output in a terminal; raw JSON when piped or redirected.
    """
    return sys.stdout.isatty()


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
    total = result.get("total", len(results))
    # Real key is "current_page"; fall back to "page" for forward-compat.
    page = result.get("current_page") or result.get("page", 1)
    page_size = result.get("page_size", len(results)) or 1

    title = f"Search: [bold cyan]{query}[/bold cyan]" if query else "Search results"
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
        item_title = item.get("title") or item.get("permalink", "")
        permalink = item.get("permalink", "")
        score = item.get("score")
        score_str = f"{score:.2f}" if score is not None else ""
        # Prefer matched_chunk as the most relevant snippet; fall back to content.
        raw_snippet = item.get("matched_chunk") or item.get("content") or ""
        # Truncate to ~200 chars so the table stays readable.
        snippet = raw_snippet[:200].replace("\n", " ") if raw_snippet else ""
        table.add_row(item_type, item_title, score_str, permalink, snippet)

    console.print(Panel(table, title=title, subtitle=subtitle, expand=False))


def _display_read_note(result: dict[str, Any]) -> None:
    """Render read-note result: header panel + rendered Markdown content."""
    title = result.get("title", "")
    permalink = result.get("permalink", "")
    content = result.get("content", "")

    header = Text()
    header.append(title, style="bold cyan")
    if permalink:
        header.append(f"  [{permalink}]", style="dim green")

    console.print(Panel(header, expand=False))

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

    label = f"[bold cyan]{uri}[/bold cyan]" if uri else "Context"
    tree = Tree(f"[bold]Context:[/bold] {label}")

    if not context_items:
        tree.add("[dim]No related content found.[/dim]")
    else:
        for context_result in context_items:
            # --- Primary result node ---
            primary = context_result.get("primary_result", {})
            p_title = primary.get("title") or primary.get("permalink", "")
            p_type = primary.get("type", "")
            primary_label = f"[cyan]{p_title}[/cyan]"
            if p_type:
                primary_label = f"[dim]{p_type}[/dim]  {primary_label}"
            primary_node = tree.add(primary_label)

            # --- Related items as children ---
            related: list[dict[str, Any]] = list(context_result.get("related_results", []))
            for rel_item in related:
                rel_title = rel_item.get("title") or rel_item.get("permalink", "")
                rel_type = rel_item.get("type", "")
                relation = rel_item.get("relation_type", "")

                parts = []
                if relation:
                    parts.append(f"[yellow]{relation}[/yellow]")
                if rel_type:
                    parts.append(f"[dim]{rel_type}[/dim]")
                parts.append(f"[cyan]{rel_title}[/cyan]")
                primary_node.add(" ".join(parts))

    # Count total related items across all primary results.
    total_related = sum(len(cr.get("related_results", [])) for cr in context_items)
    subtitle = f"{len(context_items)} primary  •  {total_related} related"
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
        item_title = item.get("title") or item.get("permalink", "")
        permalink = item.get("permalink", "")
        updated = str(item.get("updated_at") or item.get("created_at") or "")
        table.add_row(item_type, item_title, permalink, updated)

    console.print(Panel(table, title="Recent Activity", expand=False))


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

    Displays formatted Markdown output by default when run in a terminal.
    Use --json for raw machine-readable output.

    Examples:

    bm tool read-note my-note
    bm tool read-note my-note --include-frontmatter
    bm tool read-note my-note --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import read_note as mcp_read_note

    try:
        validate_routing_flags(local, cloud)

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

        # Trigger: --json flag or non-TTY stdout (piped output).
        # Why: scripts and downstream tools need parseable JSON; Rich markup
        #      would corrupt those pipelines.
        # Outcome: raw JSON for machine consumers; formatted display for humans.
        if json_output or not _use_rich() or isinstance(result, str):
            _print_json(result)
        else:
            _display_read_note(result)
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

    Displays a Rich tree view by default when run in a terminal.
    Use --json for raw machine-readable output.

    Examples:

    bm tool build-context memory://specs/search
    bm tool build-context specs/search --depth 2 --timeframe 30d
    bm tool build-context memory://specs/search --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import build_context as mcp_build_context

    try:
        validate_routing_flags(local, cloud)

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

        # Trigger: --json flag or non-TTY stdout (piped output).
        # Why: scripts and downstream tools need parseable JSON; Rich markup
        #      would corrupt those pipelines.
        # Outcome: raw JSON for machine consumers; formatted display for humans.
        if json_output or not _use_rich() or isinstance(result, str):
            _print_json(result)
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

    Displays a formatted table by default when run in a terminal.
    Use --json for raw machine-readable output.

    Examples:

    bm tool recent-activity
    bm tool recent-activity --timeframe 30d --page-size 20
    bm tool recent-activity --type entity --type observation
    bm tool recent-activity --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import recent_activity as mcp_recent_activity

    try:
        validate_routing_flags(local, cloud)

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

        # Trigger: --json flag or non-TTY stdout (piped output).
        # Why: scripts and downstream tools need parseable JSON; Rich markup
        #      would corrupt those pipelines.
        # Outcome: raw JSON for machine consumers; formatted display for humans.
        if json_output or not _use_rich() or isinstance(result, str):
            _print_json(result)
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

    Displays a formatted table by default when run in a terminal.
    Use --json for raw machine-readable output.

    Examples:

    bm tool search-notes "my query"
    bm tool search-notes --permalink "specs/*"
    bm tool search-notes --tag python --tag async
    bm tool search-notes --meta status=draft
    bm tool search-notes "auth" --entity-type observation --category requirement
    bm tool search-notes "my query" --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import search_notes as mcp_search

    try:
        validate_routing_flags(local, cloud)

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

        # Trigger: --json flag or non-TTY stdout (piped output).
        # Why: scripts and downstream tools need parseable JSON; Rich markup
        #      would corrupt those pipelines.
        # Outcome: raw JSON for machine consumers; formatted display for humans.
        if json_output or not _use_rich():
            _print_json(result)
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
