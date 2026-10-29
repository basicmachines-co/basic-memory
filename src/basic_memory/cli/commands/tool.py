"""`bm tool`: the 1.0 MCP tools from the shell.

Each subcommand calls one `bm_*` tool. Flags are the tool's parameter names in kebab
case, and arguments pass through `pydantic.validate_call`, so the shell gets the same
validation and coercion an MCP client gets: `--filters` takes the same JSON object,
`--mode` the same choices, and a bad value fails with the same message.

Output is the tool's own text result, or its JSON result with `--json`. A refusal or
an invalid argument prints to stderr and exits 1.
"""

import json
import sys
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager

# MCP tool functions are imported inside each command: importing
# basic_memory.mcp.tools loads the entire tool stack (fastmcp, mcp SDK,
# SQLAlchemy), which would slow every CLI invocation, including --help (#886).

tool_app = typer.Typer(no_args_is_help=True)
app.add_typer(tool_app, name="tool", help="Call the bm_* MCP tools from the shell")


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
PageOption = Annotated[int, typer.Option(help="Page number")]
PageSizeOption = Annotated[int, typer.Option(help="Results per page (1-100)")]
ExpectedChecksumOption = Annotated[
    Optional[str],
    typer.Option(help="Act only while the note still has this checksum"),
]


# --- Running a tool ---


def run_tool(
    tool: Callable[..., Coroutine[Any, Any, Any]],
    *,
    project: str | None,
    json_output: bool,
    local: bool,
    cloud: bool,
    default_to_config_project: bool = True,
    **arguments: Any,
) -> None:
    """Call a bm_* tool with MCP argument validation and print its result."""
    # Deferred with the tool stack (#886).
    from fastmcp.exceptions import ToolError
    from pydantic import ValidationError, validate_call

    try:
        validate_routing_flags(local, cloud)
    except ValueError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1)

    # Trigger: no --project on a command whose tool needs one.
    # Why: MCP callers must name a project, but a person at a shell has a default
    #      project in config, and every other `bm` command already uses it.
    # Outcome: the configured default, or a clear error when none is set.
    if project is None and default_to_config_project:
        project = ConfigManager().config.default_project
        if project is None:
            typer.echo("Error: no default project is set; pass --project.", err=True)
            raise typer.Exit(1)
    arguments["project"] = project

    # Only arguments the user supplied are passed, so each tool's own default applies.
    supplied = {name: value for name, value in arguments.items() if value is not None}
    validated_tool = validate_call(tool)
    try:
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                validated_tool(
                    **supplied,
                    output_format="json" if json_output else "text",
                    # Outside an MCP request there is no FastMCP context to inject.
                    context=None,
                )
            )
    except ValidationError as error:
        for problem in error.errors():
            flag = "--" + "-".join(str(part) for part in problem["loc"]).replace("_", "-")
            typer.echo(f"Error: {flag}: {problem['msg']}", err=True)
        raise typer.Exit(1)
    except ToolError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1)

    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    else:
        print(result)


# --- Notes ---


@tool_app.command("read")
def read(
    identifier: Annotated[str, typer.Argument(help="Path, permalink, memory:// URL, or title")],
    section: Annotated[Optional[str], typer.Option(help="A heading to read")] = None,
    start_line: Annotated[Optional[int], typer.Option(help="First line (1-based)")] = None,
    end_line: Annotated[Optional[int], typer.Option(help="Last line, inclusive")] = None,
    max_tokens: Annotated[Optional[int], typer.Option(help="Approximate token budget")] = None,
    include_frontmatter: Annotated[bool, typer.Option(help="Include the YAML frontmatter")] = True,
    include_content: Annotated[
        bool, typer.Option(help="False returns only metadata and the checksum")
    ] = True,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Read a note or file (bm_read).

    Examples:

    bm tool read notes/plan.md
    bm tool read "Q4 Plan" --section Decisions
    bm tool read notes/plan.md --start-line 1 --end-line 40
    """
    from basic_memory.mcp.tools import bm_read

    run_tool(
        bm_read,
        identifier=identifier,
        section=section,
        start_line=start_line,
        end_line=end_line,
        max_tokens=max_tokens,
        include_frontmatter=include_frontmatter,
        include_content=include_content,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("write")
def write(
    content: Annotated[
        Optional[str], typer.Option(help="The note's Markdown; read from stdin when omitted")
    ] = None,
    identifier: Annotated[Optional[str], typer.Option(help="Exact file path to write")] = None,
    title: Annotated[Optional[str], typer.Option(help="Note title")] = None,
    directory: Annotated[Optional[str], typer.Option(help="Folder for a titled note")] = None,
    note_type: Annotated[Optional[str], typer.Option(help="Frontmatter type")] = None,
    tags: Annotated[Optional[list[str]], typer.Option(help="Tag (repeatable)")] = None,
    metadata: Annotated[
        Optional[str], typer.Option(help="Extra frontmatter as a JSON object")
    ] = None,
    overwrite: Annotated[
        Optional[bool], typer.Option("--overwrite", help="Replace an existing note")
    ] = None,
    expected_checksum: ExpectedChecksumOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Create a note, or replace one with --overwrite (bm_write).

    Examples:

    bm tool write --identifier notes/plan.md --content "# Plan"
    bm tool write --title "Q4 Plan" --directory plans --note-type plan < plan.md
    bm tool write --identifier notes/plan.md --overwrite --expected-checksum <checksum> < plan.md
    """
    from basic_memory.mcp.tools import bm_write

    # Trigger: no --content flag.
    # Why: notes are usually longer than a shell argument; piping keeps them intact.
    # Outcome: read the note from stdin, or fail when stdin is a terminal.
    if content is None:
        if sys.stdin.isatty():  # pragma: no cover - CliRunner stdin is never a terminal
            typer.echo("Error: pass --content or pipe the note on stdin.", err=True)
            raise typer.Exit(1)
        content = sys.stdin.read()

    run_tool(
        bm_write,
        content=content,
        identifier=identifier,
        title=title,
        directory=directory,
        note_type=note_type,
        tags=tags,
        metadata=metadata,
        overwrite=overwrite,
        expected_checksum=expected_checksum,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("edit")
def edit(
    identifier: Annotated[str, typer.Argument(help="The note to edit")],
    command: Annotated[
        str,
        typer.Option(
            help="append, prepend, find_replace, replace_section, insert_before_section, "
            "insert_after_section, or set_metadata"
        ),
    ],
    content: Annotated[Optional[str], typer.Option(help="Text to add or substitute")] = None,
    section: Annotated[Optional[str], typer.Option(help="Heading for section commands")] = None,
    find_text: Annotated[Optional[str], typer.Option(help="Text to replace")] = None,
    expected_replacements: Annotated[
        Optional[int], typer.Option(help="For find_replace: required match count")
    ] = None,
    replace_subsections: Annotated[
        Optional[bool], typer.Option(help="For replace_section: replace nested headings too")
    ] = None,
    metadata: Annotated[
        Optional[str], typer.Option(help="Frontmatter changes as a JSON object")
    ] = None,
    expected_checksum: ExpectedChecksumOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Change part of a note (bm_edit).

    Examples:

    bm tool edit notes/plan.md --command append --content "- [next] ship it"
    bm tool edit notes/plan.md --command find_replace --find-text draft --content final
    bm tool edit notes/plan.md --command replace_section --section Status --content "Done."
    """
    from basic_memory.mcp.tools import bm_edit

    run_tool(
        bm_edit,
        identifier=identifier,
        command=command,
        content=content,
        section=section,
        find_text=find_text,
        expected_replacements=expected_replacements,
        replace_subsections=replace_subsections,
        metadata=metadata,
        expected_checksum=expected_checksum,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("move")
def move(
    destination: Annotated[str, typer.Option(help="New path for the note or folder")],
    identifier: Annotated[Optional[str], typer.Option(help="The note to move")] = None,
    path: Annotated[Optional[str], typer.Option(help="The folder to move")] = None,
    expected_checksum: ExpectedChecksumOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Move or rename a note or folder (bm_move).

    Examples:

    bm tool move --identifier notes/plan.md --destination plans/q4.md
    bm tool move --path drafts --destination archive/drafts
    """
    from basic_memory.mcp.tools import bm_move

    run_tool(
        bm_move,
        destination=destination,
        identifier=identifier,
        path=path,
        expected_checksum=expected_checksum,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("delete")
def delete(
    identifier: Annotated[Optional[str], typer.Option(help="The note to delete")] = None,
    path: Annotated[Optional[str], typer.Option(help="The folder to delete")] = None,
    recursive: Annotated[
        bool, typer.Option("--recursive", help="Required to delete a folder")
    ] = False,
    expected_checksum: ExpectedChecksumOption = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Delete a note, or a folder with --recursive (bm_delete).

    Examples:

    bm tool delete --identifier notes/old-draft.md
    bm tool delete --path drafts --recursive
    """
    from basic_memory.mcp.tools import bm_delete

    run_tool(
        bm_delete,
        identifier=identifier,
        path=path,
        recursive=recursive,
        expected_checksum=expected_checksum,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


# --- Finding ---


@tool_app.command("list")
def list_(
    path: Annotated[str, typer.Argument(help="The folder to list")] = "/",
    glob: Annotated[Optional[str], typer.Option(help="File-name glob, e.g. '*.md'")] = None,
    sort: Annotated[Optional[str], typer.Option(help="File order")] = None,
    page: PageOption = 1,
    page_size: PageSizeOption = 10,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """List one folder (bm_list).

    Examples:

    bm tool list
    bm tool list plans --glob "*.md"
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


@tool_app.command("find")
def find(
    path: Annotated[str, typer.Argument(help="The folder to search under")] = "/",
    name: Annotated[Optional[str], typer.Option(help="File-name glob, e.g. '*.md'")] = None,
    filters: Annotated[
        Optional[str], typer.Option(help="Frontmatter filters as a JSON object")
    ] = None,
    fields: Annotated[
        Optional[list[str]], typer.Option(help="Frontmatter field to return (repeatable)")
    ] = None,
    depth: Annotated[Optional[int], typer.Option(help="Folder levels to search (1-10)")] = None,
    page: PageOption = 1,
    page_size: PageSizeOption = 10,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Find files by name or notes by frontmatter, recursively (bm_find).

    Examples:

    bm tool find --name "*.md"
    bm tool find --filters '{"type": "task", "status": "active"}' --fields status
    """
    from basic_memory.mcp.tools import bm_find

    run_tool(
        bm_find,
        path=path,
        name=name,
        filters=filters,
        fields=fields,
        depth=depth,
        page=page,
        page_size=page_size,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("search")
def search(
    query: Annotated[Optional[str], typer.Argument(help="What to search for")] = None,
    mode: Annotated[
        Optional[str], typer.Option(help="hybrid, text, semantic, title, or permalink")
    ] = None,
    filters: Annotated[
        Optional[str], typer.Option(help="Frontmatter filters as a JSON object")
    ] = None,
    note_types: Annotated[
        Optional[list[str]], typer.Option(help="Frontmatter type (repeatable)")
    ] = None,
    items: Annotated[
        Optional[list[str]], typer.Option(help="note, observation, or relation (repeatable)")
    ] = None,
    categories: Annotated[
        Optional[list[str]], typer.Option(help="Observation category (repeatable)")
    ] = None,
    since: Annotated[Optional[str], typer.Option(help="How far back: '7d', 'last week'")] = None,
    lines: Annotated[
        Optional[int], typer.Option(help="Return matching lines with this much context")
    ] = None,
    max_matches: Annotated[Optional[int], typer.Option(help="Matching lines per note")] = None,
    identifier: Annotated[Optional[str], typer.Option(help="Search only this note")] = None,
    path: Annotated[Optional[str], typer.Option(help="Search only this folder")] = None,
    compact: Annotated[bool, typer.Option("--compact", help="Omit bodies and excerpts")] = False,
    valid_at: Annotated[Optional[str], typer.Option(help="Facts valid on this date")] = None,
    valid_overlaps: Annotated[
        Optional[str], typer.Option(help="Facts valid during '[start,end)'")
    ] = None,
    time_kind: Annotated[Optional[str], typer.Option(help="Kind of authored time")] = None,
    min_similarity: Annotated[
        Optional[float], typer.Option(help="Semantic similarity floor (0-1)")
    ] = None,
    projects: Annotated[
        Optional[list[str]], typer.Option(help="Search this project too (repeatable)")
    ] = None,
    all_projects: Annotated[
        bool, typer.Option("--all-projects", help="Search every project")
    ] = False,
    page: PageOption = 1,
    page_size: PageSizeOption = 10,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Search notes, ranked by relevance (bm_search).

    Examples:

    bm tool search "auth decision"
    bm tool search --filters '{"status": "draft"}' --note-types plan
    bm tool search "retry" --lines 2 --path specs
    bm tool search "postgres" --all-projects --since 30d
    """
    from basic_memory.mcp.tools import bm_search

    run_tool(
        bm_search,
        query=query,
        mode=mode,
        filters=filters,
        note_types=note_types,
        items=items,
        categories=categories,
        since=since,
        lines=lines,
        max_matches=max_matches,
        identifier=identifier,
        path=path,
        compact=compact,
        valid_at=valid_at,
        valid_overlaps=valid_overlaps,
        time_kind=time_kind,
        min_similarity=min_similarity,
        projects=projects,
        all_projects=all_projects,
        page=page,
        page_size=page_size,
        project=project,
        # --projects and --all-projects choose the scope themselves.
        default_to_config_project=not (projects or all_projects),
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("context")
def context(
    identifier: Annotated[str, typer.Argument(help="Note or memory:// URL to start from")],
    depth: Annotated[Optional[int], typer.Option(help="Relation hops to follow (1-5)")] = None,
    since: Annotated[Optional[str], typer.Option(help="How far back: '7d', 'last week'")] = None,
    max_related: Annotated[Optional[int], typer.Option(help="Most related results")] = None,
    compact: Annotated[bool, typer.Option("--compact", help="Omit bodies")] = False,
    page: PageOption = 1,
    page_size: PageSizeOption = 10,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Follow a note's relations (bm_context).

    Examples:

    bm tool context specs/search
    bm tool context memory://specs/* --depth 2 --since 30d
    """
    from basic_memory.mcp.tools import bm_context

    run_tool(
        bm_context,
        identifier=identifier,
        depth=depth,
        since=since,
        max_related=max_related,
        compact=compact,
        page=page,
        page_size=page_size,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


# --- Projects and schemas ---


@tool_app.command("project")
def project_(
    command: Annotated[str, typer.Argument(help="list, create, or recent")],
    name: Annotated[Optional[str], typer.Option(help="For create: the project name")] = None,
    path: Annotated[Optional[str], typer.Option(help="For create: the project folder")] = None,
    set_default: Annotated[
        bool, typer.Option("--set-default", help="For create: make it the default")
    ] = False,
    workspace: Annotated[Optional[str], typer.Option(help="Cloud workspace slug")] = None,
    since: Annotated[Optional[str], typer.Option(help="For recent: how far back")] = None,
    items: Annotated[
        Optional[list[str]], typer.Option(help="For recent: note, observation, or relation")
    ] = None,
    page: PageOption = 1,
    page_size: PageSizeOption = 10,
    project: Annotated[
        Optional[str], typer.Option(help="For recent: one project instead of all")
    ] = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """List projects, create one, or show recent activity (bm_project).

    Examples:

    bm tool project list
    bm tool project recent --since 3d
    bm tool project create --name research --path ~/research
    """
    from basic_memory.mcp.tools import bm_project

    run_tool(
        bm_project,
        command=command,
        name=name,
        path=path,
        set_default=set_default,
        workspace=workspace,
        since=since,
        items=items,
        page=page,
        page_size=page_size,
        project=project,
        # recent without --project covers every project.
        default_to_config_project=False,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )


@tool_app.command("schema")
def schema(
    command: Annotated[str, typer.Argument(help="validate, infer, or diff")],
    note_type: Annotated[Optional[str], typer.Option(help="The note type to check")] = None,
    identifier: Annotated[Optional[str], typer.Option(help="For validate: one note")] = None,
    threshold: Annotated[
        Optional[float], typer.Option(help="For infer: share of notes a field needs (0-1)")
    ] = None,
    project: ProjectOption = None,
    json_output: JsonOption = False,
    local: LocalOption = False,
    cloud: CloudOption = False,
) -> None:
    """Validate notes against a schema, infer one, or show drift (bm_schema).

    Examples:

    bm tool schema validate --note-type person
    bm tool schema infer --note-type meeting --threshold 0.5
    bm tool schema diff --note-type person
    """
    from basic_memory.mcp.tools import bm_schema

    run_tool(
        bm_schema,
        command=command,
        note_type=note_type,
        identifier=identifier,
        threshold=threshold,
        project=project,
        json_output=json_output,
        local=local,
        cloud=cloud,
    )
