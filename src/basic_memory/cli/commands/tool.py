"""CLI tool commands for Basic Memory.

Every command calls its MCP tool with output_format="json" and prints the result.
No text formatting, no separate code paths, no duplicate data fetching.
"""

import json
import sys
from typing import Annotated, Any, Dict, List, Optional

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.mcp.tools import build_context as mcp_build_context
from basic_memory.mcp.tools import edit_note as mcp_edit_note
from basic_memory.mcp.tools import fcm_export_model as mcp_fcm_export_model
from basic_memory.mcp.tools import fcm_import_model as mcp_fcm_import_model
from basic_memory.mcp.tools import fcm_rank_actions as mcp_fcm_rank_actions
from basic_memory.mcp.tools import fcm_simulate as mcp_fcm_simulate
from basic_memory.mcp.tools import graph_health as mcp_graph_health
from basic_memory.mcp.tools import graph_impact as mcp_graph_impact
from basic_memory.mcp.tools import graph_lineage as mcp_graph_lineage
from basic_memory.mcp.tools import list_memory_projects as mcp_list_projects
from basic_memory.mcp.tools import list_workspaces as mcp_list_workspaces
from basic_memory.mcp.tools import read_note as mcp_read_note
from basic_memory.mcp.tools import recent_activity as mcp_recent_activity
from basic_memory.mcp.tools import schema_diff as mcp_schema_diff
from basic_memory.mcp.tools import schema_infer as mcp_schema_infer
from basic_memory.mcp.tools import schema_validate as mcp_schema_validate
from basic_memory.mcp.tools import search_notes as mcp_search
from basic_memory.mcp.tools import write_note as mcp_write_note

tool_app = typer.Typer()
app.add_typer(tool_app, name="tool", help="Access to MCP tools via CLI")

VALID_EDIT_OPERATIONS = ["append", "prepend", "find_replace", "replace_section"]


# --- Shared helpers ---


def _print_json(result: Any) -> None:
    """Print a result as formatted JSON."""
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))


def _parse_json_option(raw_value: Optional[str], option_name: str) -> Any:
    """Parse a JSON CLI option with deterministic error handling."""
    if raw_value is None:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON for {option_name}: {exc}", err=True)
        raise typer.Exit(1)


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
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to write to. If not provided, the default project will be used."
        ),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Create or update a markdown note. Content can be provided via --content or stdin.

    Examples:

    bm tool write-note --title "My Note" --folder "notes" --content "Note content"
    echo "content" | bm tool write-note --title "My Note" --folder "notes"
    bm tool write-note --title "My Note" --folder "notes" --local
    """
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
                    workspace=workspace,
                    tags=tags,
                    output_format="json",
                )
            )
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
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Read a markdown note from the knowledge base.

    Examples:

    bm tool read-note my-note
    bm tool read-note my-note --include-frontmatter
    bm tool read-note my-note --page 2 --page-size 5
    """
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_read_note(
                    identifier=identifier,
                    project=project,
                    workspace=workspace,
                    page=page,
                    page_size=page_size,
                    include_frontmatter=include_frontmatter,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during read_note: {e}", err=True)
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
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
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
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_edit_note(
                    identifier=identifier,
                    operation=operation,
                    content=content,
                    project=project,
                    workspace=workspace,
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
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get context needed to continue a discussion.

    Examples:

    bm tool build-context memory://specs/search
    bm tool build-context specs/search --depth 2 --timeframe 30d
    """
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_build_context(
                    url=url,
                    project=project,
                    workspace=workspace,
                    depth=depth,
                    timeframe=timeframe,
                    page=page,
                    page_size=page_size,
                    max_related=max_related,
                    output_format="json",
                )
            )
        _print_json(result)
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
    page_size: int = typer.Option(50, "--page-size", help="Number of results per page"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get recent activity across the knowledge base.

    Examples:

    bm tool recent-activity
    bm tool recent-activity --timeframe 30d --page-size 20
    bm tool recent-activity --type entity --type observation
    """
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_recent_activity(
                    type=type,  # pyright: ignore[reportArgumentType]
                    depth=depth if depth is not None else 1,
                    timeframe=timeframe if timeframe is not None else "7d",
                    page=page,
                    page_size=page_size,
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during recent_activity: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("graph-lineage")
def graph_lineage(
    start: Annotated[str, typer.Argument(help="Start node identifier or memory:// reference")],
    goal: Annotated[
        Optional[str],
        typer.Option("--goal", help="Optional goal node identifier for targeted lineage"),
    ] = None,
    max_hops: int = typer.Option(4, "--max-hops", help="Maximum traversal hops (1-6)"),
    relation_filters: Annotated[
        Optional[List[str]],
        typer.Option("--relation-filter", help="Relation filters (repeatable)"),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get graph lineage paths from a start node."""
    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_graph_lineage(
                    start=start,
                    goal=goal,
                    max_hops=max_hops,
                    relation_filters=relation_filters or [],
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during graph_lineage: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("graph-impact")
def graph_impact(
    target: Annotated[str, typer.Argument(help="Target node identifier or memory:// reference")],
    horizon: int = typer.Option(2, "--horizon", help="Impact horizon in hops (1-4)"),
    relation_filters: Annotated[
        Optional[List[str]],
        typer.Option("--relation-filter", help="Relation filters (repeatable)"),
    ] = None,
    include_reasons: bool = typer.Option(
        True,
        "--include-reasons/--no-include-reasons",
        help="Include reason strings in impact output",
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get impact radius for a target node."""
    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_graph_impact(
                    target=target,
                    horizon=horizon,
                    relation_filters=relation_filters or [],
                    include_reasons=include_reasons,
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during graph_impact: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("graph-health")
def graph_health(
    scope: Annotated[Optional[str], typer.Option("--scope", help="Optional scope prefix")] = None,
    timeframe: Annotated[
        Optional[str], typer.Option("--timeframe", help="Optional timeframe filter")
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get graph health metrics and issue candidates."""
    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_graph_health(
                    scope=scope,
                    timeframe=timeframe,
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during graph_health: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("fcm-simulate")
def fcm_simulate(
    actions_json: Annotated[
        str,
        typer.Option(
            "--actions-json",
            help='JSON array of actions, e.g. [{"node_id":"n1","delta":0.2}]',
        ),
    ],
    scenario_json: Annotated[
        Optional[str],
        typer.Option("--scenario-json", help="Optional JSON scenario object"),
    ] = None,
    clamp_rules_json: Annotated[
        Optional[str],
        typer.Option("--clamp-rules-json", help="Optional JSON array of clamp rules"),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Run an FCM simulation."""
    actions = _parse_json_option(actions_json, "--actions-json")
    scenario = _parse_json_option(scenario_json, "--scenario-json")
    clamp_rules = _parse_json_option(clamp_rules_json, "--clamp-rules-json")
    if not isinstance(actions, list):
        typer.echo("Invalid JSON for --actions-json: expected a JSON array", err=True)
        raise typer.Exit(1)
    if scenario is not None and not isinstance(scenario, dict):
        typer.echo("Invalid JSON for --scenario-json: expected a JSON object", err=True)
        raise typer.Exit(1)
    if clamp_rules is not None and not isinstance(clamp_rules, list):
        typer.echo("Invalid JSON for --clamp-rules-json: expected a JSON array", err=True)
        raise typer.Exit(1)

    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_fcm_simulate(
                    actions=actions,
                    scenario=scenario,
                    clamp_rules=clamp_rules,
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during fcm_simulate: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("fcm-rank-actions")
def fcm_rank_actions(
    goal: Annotated[str, typer.Argument(help="Goal node identifier")],
    constraints_json: Annotated[
        Optional[str],
        typer.Option("--constraints-json", help="Optional JSON object of ranking constraints"),
    ] = None,
    top_k: int = typer.Option(10, "--top-k", help="Number of recommendations to return"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Rank intervention actions for an FCM goal."""
    constraints = _parse_json_option(constraints_json, "--constraints-json")
    if constraints is not None and not isinstance(constraints, dict):
        typer.echo("Invalid JSON for --constraints-json: expected a JSON object", err=True)
        raise typer.Exit(1)

    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_fcm_rank_actions(
                    goal=goal,
                    constraints=constraints,
                    top_k=top_k,
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during fcm_rank_actions: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("fcm-import-model")
def fcm_import_model(
    source: Annotated[str, typer.Argument(help="Source path or URI for import payload")],
    format: Annotated[
        str,
        typer.Option("--format", help="Import format (currently csv_bundle_v1)"),
    ] = "csv_bundle_v1",
    merge_mode: Annotated[
        str,
        typer.Option("--merge-mode", help="Merge strategy: replace or upsert"),
    ] = "upsert",
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Import an FCM model."""
    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_fcm_import_model(
                    source=source,
                    format=format,  # pyright: ignore[reportArgumentType]
                    merge_mode=merge_mode,  # pyright: ignore[reportArgumentType]
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during fcm_import_model: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("fcm-export-model")
def fcm_export_model(
    format: Annotated[
        str,
        typer.Option("--format", help="Export format (currently csv_bundle_v1)"),
    ] = "csv_bundle_v1",
    selection_json: Annotated[
        Optional[str],
        typer.Option("--selection-json", help="Optional JSON object selection payload"),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Export an FCM model."""
    selection = _parse_json_option(selection_json, "--selection-json")
    if selection is not None and not isinstance(selection, dict):
        typer.echo("Invalid JSON for --selection-json: expected a JSON object", err=True)
        raise typer.Exit(1)

    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_fcm_export_model(
                    format=format,  # pyright: ignore[reportArgumentType]
                    selection=selection,
                    project=project,
                    workspace=workspace,
                    output_format="json",
                )
            )
        _print_json(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during fcm_export_model: {e}", err=True)
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
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Search across all content in the knowledge base.

    Examples:

    bm tool search-notes "my query"
    bm tool search-notes --permalink "specs/*"
    bm tool search-notes --tag python --tag async
    bm tool search-notes --meta status=draft
    """
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
                    workspace=workspace,
                    search_type=search_type,
                    output_format="json",
                    page=page,
                    after_date=after_date,
                    page_size=page_size,
                    note_types=note_types,
                    entity_types=entity_types,
                    metadata_filters=metadata_filters,
                    tags=tags,
                    status=status,
                )
            )

        # MCP tool may return a string error message
        if isinstance(result, str):
            typer.echo(result, err=True)
            raise typer.Exit(1)

        _print_json(result)
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
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
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
                    workspace=workspace,
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
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
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
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_schema_infer(
                    note_type=note_type,
                    threshold=threshold,
                    project=project,
                    workspace=workspace,
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
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
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
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_schema_diff(
                    note_type=note_type,
                    project=project,
                    workspace=workspace,
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
