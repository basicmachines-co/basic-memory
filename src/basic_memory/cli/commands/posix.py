"""POSIX-style read verbs for the Basic Memory CLI (SPEC-47, #1404).

`bm cat`, `bm head`, `bm grep`, `bm ls`, `bm find` and `bm tree` are shell
spellings of the published bm_* tools, run the same way `bm tool` runs them
(see ``basic_memory.cli.commands.tool``):

- cat and head call bm_read;
- grep calls bm_search in text mode (meaning search with --semantic);
- ls calls bm_list;
- find calls bm_find, with --meta predicates translated to its `filters`;
- tree calls bm_find and draws the listing as a hierarchy.

Output is the tool's text result, or its JSON result with --json. A refusal or an
invalid argument prints to stderr and exits 1. Without --project the configured
default project is used, as with every other `bm` command.

Honesty rules (SPEC-47): these are subcommands of ``bm`` only; nothing shadows
real cat/grep on PATH.
"""

import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape as markup_escape
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.cli.commands.output import console
from basic_memory.cli.commands.tool import call_tool, run_tool
from basic_memory.config import ConfigManager

# MCP tool functions are imported inside each command: importing
# basic_memory.mcp.tools loads the entire tool stack (fastmcp, mcp SDK,
# SQLAlchemy), which would slow every CLI invocation, including --help (#886).


# --- Shared options ---

ProjectOption = Annotated[
    Optional[str],
    typer.Option(
        help="Project name, 'workspace/project', or project id. Defaults to the default project."
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print the tool's JSON result")]
LocalOption = Annotated[
    bool, typer.Option("--local", help="Force local API routing (ignore cloud mode)")
]
CloudOption = Annotated[bool, typer.Option("--cloud", help="Force cloud API routing")]
PageOption = Annotated[Optional[int], typer.Option("--page", help="Page number (1-indexed)")]
PageSizeOption = Annotated[
    Optional[int], typer.Option("--page-size", help="Results per page (1-100)")
]


def _refuse(message: str) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(1)


def _parse_lines(lines: str) -> tuple[int, Optional[int]]:
    """Parse cat's --lines forms into a (start_line, end_line) pair.

    Accepted forms, 1-indexed inclusive: "N-M" (range), "N-" (to end), "N" (one
    line). Bounds are bm_read's to check; the CLI adds only the string parse.
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
        raise _refuse(
            f'--lines must be "N-M", "N-" (to end), or "N" (one line), got {lines!r}'
        ) from None
    return start, end


# --- cat / head ---


@app.command()
def cat(
    identifier: Annotated[
        str, typer.Argument(help="Note path, permalink, memory:// URL, or title")
    ],
    lines: Annotated[
        Optional[str],
        typer.Option(
            "--lines",
            help='Line range "N-M", "N-" (to end), or "N" (one line); 1-indexed, '
            "frontmatter included",
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
            help="Approximate token budget; cuts at a section or paragraph boundary",
        ),
    ] = None,
    include_frontmatter: Annotated[
        bool,
        typer.Option(
            "--frontmatter/--no-frontmatter",
            help="Include the YAML frontmatter in a whole-note read",
        ),
    ] = True,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Print a note, optionally sliced by lines, section, or token budget (bm_read).

    Examples:

    bm cat specs/search
    bm cat specs/search --lines 20-40
    bm cat specs/search --section Decisions
    bm cat specs/search --max-tokens 500
    """
    from basic_memory.mcp.tools import bm_read

    start_line, end_line = _parse_lines(lines) if lines is not None else (None, None)
    run_tool(
        bm_read,
        identifier=identifier,
        start_line=start_line,
        end_line=end_line,
        section=section,
        max_tokens=max_tokens,
        include_frontmatter=include_frontmatter,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@app.command()
def head(
    identifier: Annotated[
        str, typer.Argument(help="Note path, permalink, memory:// URL, or title")
    ],
    n: Annotated[
        int, typer.Option("-n", "--lines", min=1, help="Number of lines to print (from line 1)")
    ] = 10,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Print the first lines of a note (bm_read with a line range).

    Examples:

    bm head specs/search
    bm head specs/search -n 3
    """
    from basic_memory.mcp.tools import bm_read

    run_tool(
        bm_read,
        identifier=identifier,
        start_line=1,
        end_line=n,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


# --- grep ---


@app.command()
def grep(
    pattern: Annotated[str, typer.Argument(help="Text to search for")],
    semantic: Annotated[
        bool,
        typer.Option("--semantic", "-s", help="Rank by meaning instead of matching keywords"),
    ] = False,
    context_lines: Annotated[
        Optional[int],
        typer.Option(
            "-C",
            "--lines",
            help="Return matching lines with this many lines of context (0-10)",
        ),
    ] = None,
    max_matches: Annotated[
        Optional[int],
        typer.Option("--max-matches", help="Matching lines per note with -C (1-100)"),
    ] = None,
    path: Annotated[Optional[str], typer.Option("--path", help="Search only this folder")] = None,
    page: PageOption = None,
    page_size: PageSizeOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Search note content by keyword, or by meaning with --semantic (bm_search).

    Keyword matching is the default: nearest-neighbour ranking always returns
    something, so a grep for a term that appears nowhere would list unrelated
    notes instead of nothing (#1685).

    Examples:

    bm grep "retry" -C 3
    bm grep --semantic "auth token rotation"
    bm grep "deploy checklist" --path specs --json
    """
    from basic_memory.mcp.tools import bm_search

    run_tool(
        bm_search,
        query=pattern,
        mode="hybrid" if semantic else "text",
        lines=context_lines,
        max_matches=max_matches,
        path=path,
        page=page,
        page_size=page_size,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


# --- ls ---


@app.command()
def ls(
    path: Annotated[str, typer.Argument(help="Folder to list")] = "/",
    glob: Annotated[
        Optional[str], typer.Option("--glob", help="File-name glob, e.g. '*.md'")
    ] = None,
    sort: Annotated[Optional[str], typer.Option("--sort", help="File order")] = None,
    page: PageOption = None,
    page_size: PageSizeOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """List the immediate contents of one folder (bm_list).

    `bm project list` lists the projects themselves.

    Examples:

    bm ls
    bm ls specs
    bm ls notes --page 2
    """
    from basic_memory.mcp.tools import bm_list

    run_tool(
        bm_list,
        path=path,
        glob=glob,
        sort=sort,
        page=page,
        page_size=page_size,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


# --- find ---


@app.command()
def find(
    path: Annotated[str, typer.Argument(help="Folder to start from")] = "/",
    name: Annotated[
        Optional[str], typer.Option("--name", help='File-name glob, e.g. "*.md"')
    ] = None,
    depth: Annotated[
        Optional[int], typer.Option("--depth", help="Folder levels to search (1-10)")
    ] = None,
    meta: Annotated[
        Optional[list[str]],
        typer.Option(
            "--meta",
            help=(
                "Frontmatter predicate, repeatable: 'status=active', 'confidence>0.6', "
                "'priority in high,critical', 'tags has security', 'score between 0.3,0.8', "
                "'owner=null' (key missing or null). PATH still scopes the query"
            ),
        ),
    ] = None,
    fields: Annotated[
        Optional[str],
        typer.Option(
            "--fields",
            help='Comma-separated frontmatter fields to show per hit, e.g. "title,priority" '
            "(needs --meta)",
        ),
    ] = None,
    page: PageOption = None,
    page_size: PageSizeOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Find files by name glob, or notes by frontmatter, recursively (bm_find).

    Examples:

    bm find --name "*.md"
    bm find specs --depth 3
    bm find specs --meta "status=active" --meta "confidence>0.6" --fields title,priority
    """
    from basic_memory.cli.commands.find_predicates import parse_meta_predicates
    from basic_memory.mcp.tools import bm_find

    try:
        filters = parse_meta_predicates(meta) if meta is not None else None
    except ValueError as error:
        raise _refuse(str(error)) from None
    run_tool(
        bm_find,
        path=path,
        name=name,
        depth=depth,
        filters=filters,
        # The comma form is the CLI's; bm_find validates the names themselves.
        fields=[item.strip() for item in fields.split(",")] if fields is not None else None,
        page=page,
        page_size=page_size,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


# --- tree ---


@dataclass
class _TreeEntry:
    """One rebuilt tree node: whether it is a directory, and its children."""

    is_dir: bool
    children: dict[str, "_TreeEntry"] = field(default_factory=dict)


def _build_tree(nodes: list[dict[str, Any]], root: str) -> dict[str, _TreeEntry]:
    """Rebuild a hierarchy from bm_find's flat page of directory nodes.

    The listing is flat; nesting is recovered from each node's directory_path
    segments. A glob can return files without their parent directories, so
    intermediate segments are synthesized as directories.
    """
    root_prefix = root.strip("/")
    top: dict[str, _TreeEntry] = {}
    for node in nodes:
        raw_path = str(node["directory_path"]).strip("/")
        if root_prefix:
            if raw_path == root_prefix:
                continue
            raw_path = raw_path.removeprefix(f"{root_prefix}/")
        segments = raw_path.split("/")
        level = top
        for depth, segment in enumerate(segments):
            is_leaf = depth == len(segments) - 1
            entry = level.get(segment)
            if entry is None:
                entry = _TreeEntry(is_dir=not is_leaf or node["type"] == "directory")
                level[segment] = entry
            elif is_leaf and node["type"] == "directory":
                # A synthesized intermediate may already exist; the node's own
                # listing is authoritative about it being a directory.
                entry.is_dir = True
            level = entry.children
    return top


def _listing_root(path: str, project: str | None) -> str:
    """The folder bm_find's node paths are relative to, in their own spelling.

    A path that starts with the project's name ('My Research/specs') comes back
    qualified with the project's permalink ('/my-research/specs/...'), so the root
    stripped to rebuild the hierarchy must carry that spelling too, not the
    caller's. Any other path comes back as typed.
    """
    # Deferred: the identifier helpers load the MCP routing stack (#886).
    from basic_memory.mcp.project_context_identifiers import split_project_permalink_prefix
    from basic_memory.utils import generate_permalink

    if project is None:
        return path
    claimed = split_project_permalink_prefix(path.strip("/"), [generate_permalink(project)])
    if claimed is None:
        return path
    permalink, remainder = claimed
    return f"{permalink}/{remainder}" if remainder else permalink


def _add_tree_branches(branch: Tree, entries: dict[str, _TreeEntry]) -> None:
    for segment, entry in entries.items():
        label = markup_escape(segment + ("/" if entry.is_dir else ""))
        child = branch.add(f"[bold]{label}[/bold]" if entry.is_dir else label)
        _add_tree_branches(child, entry.children)


@app.command()
def tree(
    path: Annotated[str, typer.Argument(help="Folder to start from")] = "/",
    name: Annotated[
        Optional[str], typer.Option("--name", help='File-name glob, e.g. "*.md"')
    ] = None,
    depth: Annotated[
        Optional[int], typer.Option("--depth", help="Folder levels to show (1-10)")
    ] = None,
    page: PageOption = None,
    page_size: Annotated[int, typer.Option("--page-size", help="Entries per page (1-100)")] = 100,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Show a folder hierarchy (bm_find drawn as a tree).

    Examples:

    bm tree
    bm tree specs --depth 2
    bm tree --name "*.md"
    """
    from basic_memory.mcp.tools import bm_find

    listing = call_tool(
        bm_find,
        path=path,
        name=name,
        depth=depth,
        page=page,
        page_size=page_size,
        project=project,
        output_format="json",
        local=local,
        cloud=cloud,
    )
    if json_output:
        print(json.dumps(listing, indent=2, ensure_ascii=True, default=str))
        return

    drawn = Tree(f"[bold cyan]{markup_escape(path)}[/bold cyan]")
    entries = _build_tree(
        listing["nodes"],
        _listing_root(path, project or ConfigManager().config.default_project),
    )
    if not entries:
        drawn.add("[dim]empty[/dim]")
    _add_tree_branches(drawn, entries)
    console.print(drawn)
    if listing["has_more"]:
        console.print(f"[dim]… more entries (page {listing['page']}; use --page)[/dim]")
