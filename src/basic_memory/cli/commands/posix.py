"""POSIX-style read verbs for the Basic Memory CLI (SPEC-47, #1404).

Seven top-level verbs — ``bm cat``, ``bm grep``, ``bm ls``, ``bm find``,
``bm tail``, ``bm head``, ``bm tree`` — the human/shell half of the POSIX
surface. Each is a thin frontend over the same translation layer as the MCP
posix tools: the CLI awaits the tool functions in
``basic_memory.mcp.tools.posix_tools`` directly (the repo's established
CLI↔MCP sharing pattern, same as every ``bm tool`` command), so there is one
request/translation layer with two frontends. ``head`` and ``tree`` have no
MCP counterpart; they are pure client-side recombinations of ``cat`` and
``find``.

These verbs are deliberately not gated on ``enable_posix_tools``: a human
typing ``bm cat`` is explicit intent. That config flag gates only the
agent-facing MCP tool listing (``set_posix_tools_visibility`` in
``basic_memory.mcp.server`` transforms the server's tool list); direct
function calls bypass the listing entirely, which is what this module relies
on.

Honesty rules (SPEC-47): these are subcommands of ``bm`` only — nothing
shadows real cat/grep on PATH, and there is no shell-redirection magic.
Output follows the ``bm tool`` contract: Rich rendering on a TTY, stable JSON
on ``--json`` or when piped, undecorated text with ``--plain``.
"""

import json
import sys
from dataclasses import dataclass, field
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape as markup_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.cli.commands.tool import (
    _display_read_note,
    _display_search_results,
    _plain_search_results,
    _print_json,
    _resolve_output_mode,
    _validate_output_flags,
    console,
)

from basic_memory.schemas.directory import DEFAULT_DIRECTORY_PAGE_SIZE

# MCP tool functions are imported inside each command: importing
# basic_memory.mcp.tools loads the entire tool stack (fastmcp, mcp SDK,
# SQLAlchemy), which would slow every CLI invocation, including --help (#886).


# --- Shared option types ---
# One definition per flag so the seven verbs cannot drift apart; the help text
# matches the `bm tool` commands verbatim.

ProjectOption = Annotated[
    Optional[str],
    typer.Option(help="The project to use. If not provided, the default project will be used."),
]
ProjectIdOption = Annotated[
    Optional[str],
    typer.Option(
        "--project-id",
        help=(
            "Project external_id (UUID). Takes precedence over --project; use to "
            "disambiguate same-named projects across cloud workspaces."
        ),
    ),
]
JsonOption = Annotated[
    bool, typer.Option("--json", help="Output raw JSON instead of formatted display")
]
PlainOption = Annotated[
    bool,
    typer.Option(
        "--plain", help="Output undecorated plain text (no colors/markup), even when piped"
    ),
]
LocalOption = Annotated[
    bool, typer.Option("--local", help="Force local API routing (ignore cloud mode)")
]
CloudOption = Annotated[bool, typer.Option("--cloud", help="Force cloud API routing")]


# --- Shared helpers ---


def _parse_lines(lines: str) -> tuple[int, Optional[int]]:
    """Parse cat's --lines forms into a (start_line, end_line) pair.

    Accepted forms, 1-indexed inclusive: "N-M" (range), "N-" (to end),
    "N" (one line). Bounds validation (start >= 1, end >= start) stays in the
    cat tool itself — the CLI adds only the string parse.
    """
    start_text, separator, end_text = lines.strip().partition("-")
    try:
        start = int(start_text)
        if not separator:
            end: Optional[int] = start
        elif not end_text:
            end = None
        else:
            end = int(end_text)
    except ValueError:
        raise ValueError(
            f'--lines must be "N-M", "N-" (to end), or "N" (one line), got {lines!r}'
        ) from None
    return start, end


def _directory_page_summary(result: dict[str, Any]) -> str:
    """Describe a directory listing page without inventing a final page."""
    summary = f"page {result.get('page', 1)}  •  total {result.get('total', 0)}"
    if result.get("has_more") is True:
        summary += "  •  more available (--page)"
    return summary


# --- cat / head rendering ---


def _write_slice_footer(
    result: dict[str, Any], *, plain: bool, content_terminated: bool = True
) -> None:
    """Describe an applied slice under the content.

    Rich mode prints a dim footer on stdout; plain mode sends it to stderr so
    stdout stays pure note content (real-cat behavior, redirection-safe).
    """
    notes: list[str] = []
    start_line = result.get("start_line")
    end_line = result.get("end_line")
    if start_line is not None and end_line is not None:
        note = f"lines {start_line}-{end_line}"
        if result.get("total_lines") is not None:
            note += f" of {result['total_lines']}"
        notes.append(note)
    if result.get("section"):
        notes.append(f"section {result['section']}")
    if result.get("truncated"):
        continue_line = result.get("continue_line")
        if continue_line is not None:
            notes.append(f"truncated — continue with --lines {continue_line}-")
        else:
            notes.append("truncated")
    if not notes:
        return
    text = " • ".join(notes)
    if plain:
        # Content was written with sys.stdout.write and may not end in a
        # newline, so its last line can sit in stdout's line buffer on a TTY;
        # flush before the unbuffered stderr write or the footer prints first.
        sys.stdout.flush()
        # An unterminated slice would visually concatenate the footer onto the
        # last content line in a terminal or merged capture. Lead with the
        # newline on STDERR so stdout stays byte-exact for pipes.
        if not content_terminated:
            text = f"\n{text}"
        print(text, file=sys.stderr)
    else:
        console.print(Text(text, style="dim"))


def _render_cat(
    result: dict[str, Any], *, json_output: bool, plain: bool, include_frontmatter: bool
) -> None:
    """Render a cat/head payload in the resolved output mode."""
    mode = _resolve_output_mode(json_output, plain)
    if mode == "json":
        _print_json(result)
        return
    if mode == "plain":
        # Plain stdout is the content and nothing else — exact bytes, no added
        # newline — so `bm cat x --plain` pipes and redirects like cat(1) and
        # round-trips the file; slice info goes to stderr.
        content = result.get("content")
        text = content if isinstance(content, str) else ""
        sys.stdout.write(text)
        _write_slice_footer(result, plain=True, content_terminated=text.endswith("\n") or not text)
        return
    # cat's payload is the read_note JSON shape, so the read-note renderer applies.
    _display_read_note(result, include_frontmatter=include_frontmatter)
    _write_slice_footer(result, plain=False)


# --- ls rendering ---


def _display_ls(result: dict[str, Any], path: str) -> None:
    """Render one directory level as a Rich table."""
    nodes: list[dict[str, Any]] = list(result.get("nodes", []))
    title = f"ls [bold cyan]{markup_escape(path)}[/bold cyan]"
    subtitle = _directory_page_summary(result)

    if not nodes:
        console.print(Panel(Text("Empty directory.", style="dim"), title=title, subtitle=subtitle))
        return

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Name", style="bold cyan")
    table.add_column("Type", style="dim", width=9)
    table.add_column("Title")
    table.add_column("Permalink", style="green")
    table.add_column("Updated", style="dim")
    for node in nodes:
        # User-sourced cell text must be escaped so bracketed names ("[draft]")
        # survive Rich's markup parsing.
        name = markup_escape(str(node.get("name") or ""))
        if node.get("type") == "directory":
            name += "/"
        table.add_row(
            name,
            str(node.get("type") or ""),
            markup_escape(str(node.get("title") or "")),
            markup_escape(str(node.get("permalink") or "")),
            str(node.get("updated_at") or ""),
        )
    console.print(Panel(table, title=title, subtitle=subtitle, expand=False))


def _plain_ls(result: dict[str, Any]) -> None:
    """Render one directory level as ls -1 style lines (one path per line)."""
    for node in result.get("nodes", []):
        suffix = "/" if node.get("type") == "directory" else ""
        print(f"{node.get('directory_path', '')}{suffix}")


# --- find rendering ---


def _display_find(result: dict[str, Any], path: str, name: Optional[str]) -> None:
    """Render recursive find results as a Rich table."""
    nodes: list[dict[str, Any]] = list(result.get("nodes", []))
    title = f"find [bold cyan]{markup_escape(path)}[/bold cyan]"
    if name:
        title += f" [dim]--name {markup_escape(name)}[/dim]"
    subtitle = _directory_page_summary(result)

    if not nodes:
        console.print(Panel(Text("No matches.", style="dim"), title=title, subtitle=subtitle))
        return

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Path", style="bold cyan")
    table.add_column("Title")
    table.add_column("Permalink", style="green")
    for node in nodes:
        node_path = str(node.get("file_path") or node.get("directory_path") or "")
        table.add_row(
            markup_escape(node_path),
            markup_escape(str(node.get("title") or "")),
            markup_escape(str(node.get("permalink") or "")),
        )
    console.print(Panel(table, title=title, subtitle=subtitle, expand=False))


def _plain_find(result: dict[str, Any]) -> None:
    """Render find results as find(1) style lines (one path per line)."""
    for node in result.get("nodes", []):
        print(str(node.get("file_path") or node.get("directory_path") or ""))


# --- find --meta --fields rendering ---
# Metadata predicates flip find's payload to the search response shape; without
# --fields the shared search renderers (grep's) apply, with --fields these two
# small renderers add the projected columns so the shared ones stay untouched.


def _search_page_summary(result: dict[str, Any]) -> str:
    """Describe a search results page without inventing a final page."""
    summary = f"page {result.get('current_page', 1)}  •  total {result.get('total', 0)}"
    if result.get("has_more") is True:
        summary += "  •  more available (--page)"
    return summary


def _field_cell(value: Any) -> str:
    """Render one projected field value: strings bare, null empty, the rest compact JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def _display_find_fields(
    result: dict[str, Any], path: str, meta: list[str], fields: list[str]
) -> None:
    """Render metadata hits with their projected fields as a Rich table."""
    rows: list[dict[str, Any]] = list(result.get("results", []))
    title = (
        f"find [bold cyan]{markup_escape(path)}[/bold cyan]"
        f" [dim]--meta {markup_escape(' AND '.join(meta))}[/dim]"
    )
    subtitle = _search_page_summary(result)

    if not rows:
        console.print(Panel(Text("No matches.", style="dim"), title=title, subtitle=subtitle))
        return

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Path", style="bold cyan")
    for field_name in fields:
        table.add_column(markup_escape(field_name))
    for row in rows:
        projected = row.get("fields") or {}
        cells = [markup_escape(str(row.get("file_path") or ""))]
        cells.extend(markup_escape(_field_cell(projected.get(field_name))) for field_name in fields)
        table.add_row(*cells)
    console.print(Panel(table, title=title, subtitle=subtitle, expand=False))


def _plain_find_fields(result: dict[str, Any]) -> None:
    """Render metadata hits as file-path<TAB>compact-JSON-fields lines."""
    for row in result.get("results", []):
        fields_json = json.dumps(row.get("fields") or {}, separators=(",", ":"))
        print(f"{row.get('file_path', '')}\t{fields_json}")


# --- tail rendering ---
# tail's row shape ({type, title, permalink, file_path, created_at}) differs
# from recent-activity's payload, so it gets its own small renderers rather
# than reusing _display_recent_activity.


def _display_tail(rows: list[dict[str, Any]]) -> None:
    """Render recently changed notes as a Rich table, newest first."""
    if not rows:
        console.print(Panel(Text("No recent changes.", style="dim"), title="tail", expand=False))
        return

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("Created", style="dim")
    table.add_column("Type", style="dim", width=12)
    table.add_column("Title", style="bold cyan")
    table.add_column("Permalink", style="green")
    table.add_column("File", style="dim")
    for row in rows:
        table.add_row(
            str(row.get("created_at") or ""),
            str(row.get("type") or ""),
            markup_escape(str(row.get("title") or "")),
            markup_escape(str(row.get("permalink") or "")),
            markup_escape(str(row.get("file_path") or "")),
        )
    console.print(Panel(table, title="tail", expand=False))


def _plain_tail(rows: list[dict[str, Any]]) -> None:
    """Render recently changed notes as tab-separated lines."""
    for row in rows:
        columns = (
            str(row.get("created_at") or ""),
            str(row.get("type") or ""),
            str(row.get("title") or ""),
            str(row.get("permalink") or ""),
            str(row.get("file_path") or ""),
        )
        print("\t".join(columns))


# --- tree rendering ---


@dataclass
class _TreeEntry:
    """One rebuilt tree node: whether it is a directory, and its children."""

    is_dir: bool
    children: dict[str, "_TreeEntry"] = field(default_factory=dict)


def _build_tree(nodes: list[dict[str, Any]], root: str) -> dict[str, _TreeEntry]:
    """Rebuild a hierarchy from the API's flat page of directory nodes.

    The directory API strips children from paginated listings, so the page is
    flat; nesting is recovered from each node's directory_path segments. A glob
    filter can return files without their parent directories, so intermediate
    segments are synthesized as directories.
    """
    root_prefix = root.strip("/")
    top: dict[str, _TreeEntry] = {}
    for node in nodes:
        raw_path = str(node.get("directory_path") or node.get("file_path") or "").strip("/")
        if root_prefix:
            if raw_path == root_prefix:
                continue
            raw_path = raw_path.removeprefix(f"{root_prefix}/")
        if not raw_path:
            continue
        segments = raw_path.split("/")
        level = top
        for depth, segment in enumerate(segments):
            is_leaf = depth == len(segments) - 1
            entry = level.get(segment)
            if entry is None:
                entry = _TreeEntry(is_dir=not is_leaf or node.get("type") == "directory")
                level[segment] = entry
            elif is_leaf and node.get("type") == "directory":
                # A synthesized intermediate may already exist; the node's own
                # listing is authoritative about it being a directory.
                entry.is_dir = True
            level = entry.children
    return top


def _add_tree_branches(branch: Tree, entries: dict[str, _TreeEntry]) -> None:
    for segment, entry in entries.items():
        label = markup_escape(segment + ("/" if entry.is_dir else ""))
        child = branch.add(f"[bold]{label}[/bold]" if entry.is_dir else label)
        _add_tree_branches(child, entry.children)


def _display_tree(result: dict[str, Any], label: str, root: str) -> None:
    """Render find results as a Rich tree rooted at the search path.

    ``label`` is the caller's spelling of the root; ``root`` is the routed
    project-relative path the node paths actually start with — a qualified
    '<project>/dir' input strips its project prefix in the shared tool layer,
    so the two differ exactly when the input carried a project prefix (#1415).
    """
    entries = _build_tree(list(result.get("nodes", [])), root)
    tree = Tree(f"[bold cyan]{markup_escape(label)}[/bold cyan]")
    if not entries:
        tree.add("[dim]empty[/dim]")
    _add_tree_branches(tree, entries)
    console.print(tree)
    if result.get("has_more") is True:
        console.print(
            Text(f"… more entries (page {result.get('page', 1)}; use --page)", style="dim")
        )


def _print_plain_tree_level(entries: dict[str, _TreeEntry], depth: int) -> None:
    for segment, entry in entries.items():
        suffix = "/" if entry.is_dir else ""
        print(f"{'  ' * depth}{segment}{suffix}")
        _print_plain_tree_level(entry.children, depth + 1)


def _plain_tree(result: dict[str, Any], label: str, root: str) -> None:
    """Render the tree as two-space-indented lines; pagination note on stderr.

    ``label``/``root`` split as in ``_display_tree``: print the caller's
    spelling, strip the routed project-relative root from node paths.
    """
    print(label)
    _print_plain_tree_level(_build_tree(list(result.get("nodes", [])), root), depth=1)
    if result.get("has_more") is True:
        print(f"… more entries (page {result.get('page', 1)}; use --page)", file=sys.stderr)


# --- Commands ---


@app.command()
def cat(
    identifier: Annotated[
        str, typer.Argument(help="Note title, permalink, or memory:// URL (resolved exactly)")
    ],
    lines: Annotated[
        Optional[str],
        typer.Option(
            "--lines",
            help='Line range "N-M", "N-" (to end), or "N" (one line); 1-indexed inclusive',
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section", help='Heading slice: "Decisions", "Auth/Decisions", or "Heading[1]"'
        ),
    ] = None,
    max_tokens: Annotated[
        Optional[int],
        typer.Option(
            "--max-tokens",
            help="Approximate token budget; truncates at a section/paragraph boundary",
        ),
    ] = None,
    include_frontmatter: Annotated[
        bool,
        typer.Option(
            "--frontmatter/--no-frontmatter",
            help="Include the YAML frontmatter block (ignored for section/token slices)",
        ),
    ] = True,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Print a note's content, optionally sliced by lines, section, or token budget.

    Examples:

    bm cat specs/search
    bm cat specs/search --lines 20-40
    bm cat specs/search --section Decisions
    bm cat specs/search --max-tokens 500 --plain
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import cat as mcp_cat
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)
        start_line, end_line = _parse_lines(lines) if lines is not None else (None, None)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_cat(
                    identifier,
                    start_line=start_line,
                    end_line=end_line,
                    section=section,
                    max_tokens=max_tokens,
                    include_frontmatter=include_frontmatter,
                    project=project,
                    project_id=project_id,
                )
            )
        _render_cat(
            result, json_output=json_output, plain=plain, include_frontmatter=include_frontmatter
        )
    # ToolError is cat's strict-resolve miss ("Entity not found"); ValueError is
    # the tool's own argument validation. Both are user-facing failures.
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during cat: {e}", err=True)
            raise typer.Exit(1)
        raise


@app.command()
def head(
    identifier: Annotated[
        str, typer.Argument(help="Note title, permalink, or memory:// URL (resolved exactly)")
    ],
    n: Annotated[
        int, typer.Option("-n", "--lines", min=1, help="Number of lines to print (from line 1)")
    ] = 10,
    include_frontmatter: Annotated[
        bool,
        typer.Option("--frontmatter/--no-frontmatter", help="Include the YAML frontmatter block"),
    ] = True,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Print the first lines of a note (head(1) over `bm cat`).

    Examples:

    bm head specs/search
    bm head specs/search -n 3 --plain
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    # head is a client-side recombination of cat: same tool, fixed line range,
    # so its JSON contract is exactly cat's payload.
    from basic_memory.mcp.tools import cat as mcp_cat
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_cat(
                    identifier,
                    start_line=1,
                    end_line=n,
                    include_frontmatter=include_frontmatter,
                    project=project,
                    project_id=project_id,
                )
            )
        _render_cat(
            result, json_output=json_output, plain=plain, include_frontmatter=include_frontmatter
        )
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during head: {e}", err=True)
            raise typer.Exit(1)
        raise


@app.command()
def grep(
    pattern: Annotated[str, typer.Argument(help="Text to search for")],
    literal: Annotated[
        bool,
        typer.Option(
            "--literal", "-F", help="Literal full-text matching instead of semantic search"
        ),
    ] = False,
    page: Annotated[int, typer.Option("--page", help="Page number (1-indexed)")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Results per page")] = 10,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Search note content, semantically by default (-F for literal matching).

    Examples:

    bm grep "auth token rotation"
    bm grep -F "BASIC_MEMORY_FORCE_LOCAL"
    bm grep "deploy checklist" --page-size 20 --json
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import grep as mcp_grep
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_grep(
                    pattern,
                    literal=literal,
                    page=page,
                    page_size=page_size,
                    project=project,
                    project_id=project_id,
                )
            )
        # grep returns the search-notes response shape, so those renderers apply.
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            _print_json(result)
        elif mode == "plain":
            _plain_search_results(result, query=pattern)
        else:
            _display_search_results(result, query=pattern)
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during grep: {e}", err=True)
            raise typer.Exit(1)
        raise


# Naming note: `bm ls` lists one directory inside one project; the unrelated
# `bm project ls` lists projects at the workspace level.
@app.command()
def ls(
    path: Annotated[str, typer.Argument(help="Directory path to list")] = "/",
    page: Annotated[int, typer.Option("--page", help="Page number (1-indexed)")] = 1,
    page_size: Annotated[
        int, typer.Option("--page-size", help="Nodes per page")
    ] = DEFAULT_DIRECTORY_PAGE_SIZE,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """List the immediate contents of one directory.

    Examples:

    bm ls
    bm ls /specs
    bm ls /notes --page 2 --plain
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import ls as mcp_ls
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_ls(
                    path,
                    page=page,
                    page_size=page_size,
                    project=project,
                    project_id=project_id,
                )
            )
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            _print_json(result)
        elif mode == "plain":
            _plain_ls(result)
        else:
            _display_ls(result, path)
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during ls: {e}", err=True)
            raise typer.Exit(1)
        raise


@app.command()
def find(
    path: Annotated[str, typer.Argument(help="Directory to start from")] = "/",
    name: Annotated[
        Optional[str], typer.Option("--name", help='File-name glob, e.g. "*.md"')
    ] = None,
    depth: Annotated[
        int, typer.Option("--depth", min=1, max=10, help="Recursion depth (API bound 1-10)")
    ] = 10,
    page: Annotated[int, typer.Option("--page", help="Page number (1-indexed)")] = 1,
    page_size: Annotated[
        int, typer.Option("--page-size", help="Nodes per page")
    ] = DEFAULT_DIRECTORY_PAGE_SIZE,
    meta: Annotated[
        Optional[list[str]],
        typer.Option(
            "--meta",
            help=(
                "Metadata predicate, repeatable: 'status=active', 'confidence>0.6', "
                "'priority in high,critical', 'tags has security', 'score between 0.3,0.8', "
                "'owner=null' (key missing or null). "
                "PATH still scopes the query, by file path"
            ),
        ),
    ] = None,
    fields: Annotated[
        Optional[str],
        typer.Option(
            "--fields",
            help=(
                'Comma-separated frontmatter fields to show per hit, e.g. "title,priority" '
                "(requires --meta)"
            ),
        ),
    ] = None,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Recursively list files by name glob, or query notes by frontmatter metadata.

    Examples:

    bm find --name "*.md"
    bm find /specs --depth 3
    bm find /notes --name "auth*" --plain
    bm find /specs --meta "status=active" --meta "confidence>0.6" --fields title,priority
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import find as mcp_find
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)
        # The CLI only splits the comma form; validation (non-empty names,
        # requires --meta) lives in the shared tool layer.
        field_list = [item.strip() for item in fields.split(",")] if fields is not None else None

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_find(
                    path,
                    name=name,
                    depth=depth,
                    page=page,
                    page_size=page_size,
                    meta=meta,
                    fields=field_list,
                    project=project,
                    project_id=project_id,
                )
            )
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            _print_json(result)
        elif "results" in result:
            # --meta flips the payload to the search response shape: projected
            # fields get the dedicated renderers, otherwise grep's search
            # renderers apply.
            if field_list:
                if mode == "plain":
                    _plain_find_fields(result)
                else:
                    _display_find_fields(result, path, meta or [], field_list)
            elif mode == "plain":
                _plain_search_results(result, query=" AND ".join(meta or []))
            else:
                _display_search_results(result, query=" AND ".join(meta or []))
        elif mode == "plain":
            _plain_find(result)
        else:
            _display_find(result, path, name)
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during find: {e}", err=True)
            raise typer.Exit(1)
        raise


@app.command()
def tail(
    timeframe: Annotated[
        str, typer.Option("--timeframe", help='Time window, e.g. "7d", "yesterday"')
    ] = "7d",
    n: Annotated[
        int, typer.Option("-n", "--lines", min=1, max=100, help="Rows to show (1-100)")
    ] = 10,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Show the most recently changed notes, newest first.

    Examples:

    bm tail
    bm tail -n 20 --timeframe 1d
    bm tail --plain
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import tail as mcp_tail
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_tail(
                    timeframe=timeframe,
                    lines=n,
                    project=project,
                    project_id=project_id,
                )
            )
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            _print_json(result)
        elif mode == "plain":
            _plain_tail(result)
        else:
            _display_tail(result)
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during tail: {e}", err=True)
            raise typer.Exit(1)
        raise


@app.command()
def tree(
    path: Annotated[str, typer.Argument(help="Directory to start from")] = "/",
    name: Annotated[
        Optional[str], typer.Option("--name", help='File-name glob, e.g. "*.md"')
    ] = None,
    depth: Annotated[
        int, typer.Option("--depth", min=1, max=10, help="Recursion depth (API bound 1-10)")
    ] = 10,
    page: Annotated[int, typer.Option("--page", help="Page number (1-indexed)")] = 1,
    page_size: Annotated[
        int, typer.Option("--page-size", help="Nodes per page")
    ] = DEFAULT_DIRECTORY_PAGE_SIZE,
    json_output: JsonOption = False,
    plain: PlainOption = False,
    project: ProjectOption = None,
    project_id: ProjectIdOption = None,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Show a directory hierarchy (tree(1) over `bm find`).

    Examples:

    bm tree
    bm tree /specs --depth 2
    bm tree --name "*.md" --plain
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    # tree is a client-side recombination of find: the same flat listing, with
    # the hierarchy rebuilt from directory paths for display, so its JSON
    # contract is exactly find's payload.
    from basic_memory.mcp.tools.posix_tools import find_listing
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)

        # find returns the listing and the root its node paths are relative to
        # from one resolution. Resolving here as well cost a second project-list
        # round trip on every cloud call, because a CLI invocation has no
        # FastMCP context for the per-request cache to live in (#1421).
        with force_routing(local=local, cloud=cloud):
            result, root = run_with_cleanup(
                find_listing(
                    path,
                    name=name,
                    depth=depth,
                    page=page,
                    page_size=page_size,
                    project=project,
                    project_id=project_id,
                )
            )
        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            _print_json(result)
        elif mode == "plain":
            _plain_tree(result, path, root)
        else:
            _display_tree(result, path, root)
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during tree: {e}", err=True)
            raise typer.Exit(1)
        raise
