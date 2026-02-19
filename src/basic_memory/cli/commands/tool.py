"""CLI tool commands for Basic Memory."""

import json
import sys
from typing import Annotated, Any, List, Optional

import typer
import yaml
from loguru import logger
from rich import print as rprint

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager
from basic_memory.mcp.clients import KnowledgeClient, ResourceClient
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.base import Entity, TimeFrame
from basic_memory.schemas.memory import GraphContext, MemoryUrl, memory_url_path
from basic_memory.schemas.search import SearchItemType

# Import prompts
from basic_memory.mcp.prompts.continue_conversation import (
    continue_conversation as mcp_continue_conversation,
)
from basic_memory.mcp.tools import build_context as mcp_build_context
from basic_memory.mcp.tools import edit_note as mcp_edit_note
from basic_memory.mcp.tools import read_note as mcp_read_note
from basic_memory.mcp.tools import recent_activity as mcp_recent_activity
from basic_memory.mcp.tools import search_notes as mcp_search
from basic_memory.mcp.tools import write_note as mcp_write_note

tool_app = typer.Typer()
app.add_typer(tool_app, name="tool", help="Access to MCP tools via CLI")

VALID_EDIT_OPERATIONS = ["append", "prepend", "find_replace", "replace_section"]


# --- Frontmatter helpers ---


def _parse_opening_frontmatter(content: str) -> tuple[str, dict[str, Any] | None]:
    """Parse and strip an opening YAML frontmatter block if valid.

    Returns a tuple of (body_content_or_original, parsed_frontmatter_or_none).

    Behavior:
    - Only parses frontmatter if the first line is an opening '---' delimiter.
    - Requires a closing '---' delimiter.
    - Accepts mapping YAML only; malformed or non-mapping YAML is ignored.
    - Supports UTF-8 BOM at document start.
    """
    if not content:
        return content, None

    original_content = content
    if content.startswith("\ufeff"):
        content = content[1:]

    lines = content.splitlines(keepends=True)
    if not lines:
        return original_content, None

    if lines[0].rstrip("\r\n").strip() != "---":
        return original_content, None

    closing_index = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n").strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return original_content, None

    frontmatter_text = "".join(lines[1:closing_index])
    try:
        parsed = yaml.safe_load(frontmatter_text) if frontmatter_text else {}
    except yaml.YAMLError:
        return original_content, None

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return original_content, None

    body_content = "".join(lines[closing_index + 1 :])
    return body_content, parsed


# --- JSON output helpers ---
# These async functions bypass the MCP tool (which returns formatted strings)
# and use API clients directly to return structured data for --format json.


async def _write_note_json(
    title: str,
    content: str,
    folder: str,
    project_name: Optional[str],
    workspace: Optional[str],
    tags: Optional[List[str]],
) -> dict:
    """Write a note and return structured JSON metadata."""
    # Use the MCP tool to create/update the entity (handles create-or-update logic)
    await mcp_write_note.fn(
        title=title,
        content=content,
        directory=folder,
        project=project_name,
        workspace=workspace,
        tags=tags,
    )

    # Resolve the entity to get metadata back
    async with get_project_client(project_name, workspace) as (client, active_project):
        knowledge_client = KnowledgeClient(client, active_project.external_id)

        entity = Entity(title=title, directory=folder)
        if not entity.permalink:
            raise ValueError(f"Could not generate permalink for title={title}, folder={folder}")
        entity_id = await knowledge_client.resolve_entity(entity.permalink)
        entity = await knowledge_client.get_entity(entity_id)

        return {
            "title": entity.title,
            "permalink": entity.permalink,
            "content": content,
            "file_path": entity.file_path,
        }


async def _read_note_json(
    identifier: str,
    project_name: Optional[str],
    workspace: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    """Read a note and return structured JSON with content and metadata."""
    async with get_project_client(project_name, workspace) as (client, active_project):
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        resource_client = ResourceClient(client, active_project.external_id)

        # Try direct resolution first (works for permalinks and memory URLs)
        entity_path = memory_url_path(identifier)
        entity_id = None
        try:
            entity_id = await knowledge_client.resolve_entity(entity_path)
        except Exception:
            logger.info(f"Direct lookup failed for '{entity_path}', trying title search")

        # Fallback: title search (handles plain titles like "My Note")
        if entity_id is None:
            from basic_memory.mcp.tools.search import search_notes as mcp_search_tool

            title_results = await mcp_search_tool.fn(
                query=identifier,
                search_type="title",
                project=project_name,
                workspace=workspace,
                output_format="json",
            )
            results = title_results.get("results", []) if isinstance(title_results, dict) else []
            if results:
                result = results[0]
                permalink = result.get("permalink")
                if permalink:
                    entity_id = await knowledge_client.resolve_entity(permalink)

        if entity_id is None:
            raise ValueError(f"Could not find note matching: {identifier}")

        entity = await knowledge_client.get_entity(entity_id)
        response = await resource_client.read(entity_id, page=page, page_size=page_size)

        return {
            "title": entity.title,
            "permalink": entity.permalink,
            "content": response.text,
            "file_path": entity.file_path,
        }


async def _edit_note_json(
    identifier: str,
    operation: str,
    content: str,
    project_name: Optional[str],
    workspace: Optional[str],
    section: Optional[str],
    find_text: Optional[str],
    expected_replacements: int,
) -> dict:
    """Edit a note and return structured JSON metadata."""
    async with get_project_client(project_name, workspace) as (client, active_project):
        knowledge_client = KnowledgeClient(client, active_project.external_id)

        entity_id = await knowledge_client.resolve_entity(identifier)

        edit_data: dict[str, Any] = {
            "operation": operation,
            "content": content,
            "expected_replacements": expected_replacements,
        }
        if section:
            edit_data["section"] = section
        if find_text:
            edit_data["find_text"] = find_text

        result = await knowledge_client.patch_entity(entity_id, edit_data, fast=False)
        return {
            "title": result.title,
            "permalink": result.permalink,
            "file_path": result.file_path,
            "operation": operation,
            "checksum": result.checksum,
        }


def _validate_edit_note_args(
    operation: str, find_text: Optional[str], section: Optional[str]
) -> None:
    """Validate operation-specific required arguments for edit-note."""
    if operation not in VALID_EDIT_OPERATIONS:
        raise ValueError(
            f"Invalid operation '{operation}'. Must be one of: {', '.join(VALID_EDIT_OPERATIONS)}"
        )
    if operation == "find_replace" and not find_text:
        raise ValueError("find_text parameter is required for find_replace operation")
    if operation == "replace_section" and not section:
        raise ValueError("section parameter is required for replace_section operation")


def _is_edit_note_failure_response(result: str) -> bool:
    """Check whether the MCP edit_note text response indicates a failed edit."""
    return result.lstrip().startswith("# Edit Failed")


async def _recent_activity_json(
    type: Optional[List[SearchItemType]],
    depth: Optional[int],
    timeframe: Optional[TimeFrame],
    project_name: Optional[str] = None,
    workspace: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> list:
    """Get recent activity and return structured JSON list."""
    async with get_project_client(project_name, workspace) as (client, active_project):
        # Build query params matching the MCP tool's logic
        params: dict = {"page": page, "page_size": page_size, "max_related": 10}
        if depth:
            params["depth"] = depth
        if timeframe:
            params["timeframe"] = timeframe
        if type:
            params["type"] = [t.value for t in type]

        response = await call_get(
            client,
            f"/v2/projects/{active_project.external_id}/memory/recent",
            params=params,
        )
        activity_data = GraphContext.model_validate(response.json())

        # Extract entity results
        results = []
        for result in activity_data.results:
            pr = result.primary_result
            if pr.type == "entity":
                results.append(
                    {
                        "title": pr.title,
                        "permalink": pr.permalink,
                        "file_path": pr.file_path,
                        "created_at": str(pr.created_at) if pr.created_at else None,
                    }
                )
        return results


@tool_app.command()
def write_note(
    title: Annotated[str, typer.Option(help="The title of the note")],
    folder: Annotated[str, typer.Option(help="The folder to create the note in")],
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
    content: Annotated[
        Optional[str],
        typer.Option(
            help="The content of the note. If not provided, content will be read from stdin. This allows piping content from other commands, e.g.: cat file.md | basic-memory tools write-note"
        ),
    ] = None,
    tags: Annotated[
        Optional[List[str]], typer.Option(help="A list of tags to apply to the note")
    ] = None,
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Create or update a markdown note. Content can be provided as an argument or read from stdin.

    Content can be provided in two ways:
    1. Using the --content parameter
    2. Piping content through stdin (if --content is not provided)

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.

    Examples:

    # Using content parameter
    basic-memory tools write-note --title "My Note" --folder "notes" --content "Note content"

    # Using stdin pipe
    echo "# My Note Content" | basic-memory tools write-note --title "My Note" --folder "notes"

    # Using heredoc
    cat << EOF | basic-memory tools write-note --title "My Note" --folder "notes"
    # My Document

    This is my document content.

    - Point 1
    - Point 2
    EOF

    # Reading from a file
    cat document.md | basic-memory tools write-note --title "Document" --folder "docs"

    # Force local routing in cloud mode
    basic-memory tools write-note --title "My Note" --folder "notes" --content "..." --local
    """
    try:
        validate_routing_flags(local, cloud)

        # If content is not provided, read from stdin
        if content is None:
            # Check if we're getting data from a pipe or redirect
            if not sys.stdin.isatty():
                content = sys.stdin.read()
            else:  # pragma: no cover
                # If stdin is a terminal (no pipe/redirect), inform the user
                typer.echo(
                    "No content provided. Please provide content via --content or by piping to stdin.",
                    err=True,
                )
                raise typer.Exit(1)

        # Also check for empty content
        if content is not None and not content.strip():
            typer.echo("Empty content provided. Please provide non-empty content.", err=True)
            raise typer.Exit(1)

        # look for the project in the config
        config_manager = ConfigManager()
        project_name = None
        if project is not None:
            project_name, _ = config_manager.get_project(project)
            if not project_name:
                typer.echo(f"No project found named: {project}", err=True)
                raise typer.Exit(1)

        # use the project name, or the default from the config
        project_name = project_name or config_manager.default_project

        # content is validated non-None above (stdin or --content)
        assert content is not None

        with force_routing(local=local, cloud=cloud):
            if format == "json":
                result = run_with_cleanup(
                    _write_note_json(title, content, folder, project_name, workspace, tags)
                )
                print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
            else:
                note = run_with_cleanup(
                    mcp_write_note.fn(
                        title=title,
                        content=content,
                        directory=folder,
                        project=project_name,
                        workspace=workspace,
                        tags=tags,
                    )
                )
                rprint(note)
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
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to use for the note. If not provided, the default project will be used."
        ),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    page: int = 1,
    page_size: int = 10,
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
    strip_frontmatter: bool = typer.Option(
        False,
        "--strip-frontmatter",
        help=(
            "Strip opening YAML frontmatter from content. "
            "JSON output includes parsed frontmatter under 'frontmatter'."
        ),
    ),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Read a markdown note from the knowledge base.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    Use --strip-frontmatter to return body-only markdown content.
    """
    try:
        validate_routing_flags(local, cloud)

        # look for the project in the config
        config_manager = ConfigManager()
        project_name = None
        if project is not None:
            project_name, _ = config_manager.get_project(project)
            if not project_name:
                typer.echo(f"No project found named: {project}", err=True)
                raise typer.Exit(1)

        # use the project name, or the default from the config
        project_name = project_name or config_manager.default_project

        with force_routing(local=local, cloud=cloud):
            if format == "json":
                result = run_with_cleanup(
                    _read_note_json(identifier, project_name, workspace, page, page_size)
                )
                stripped_content, parsed_frontmatter = _parse_opening_frontmatter(result["content"])
                result["frontmatter"] = parsed_frontmatter
                if strip_frontmatter:
                    result["content"] = stripped_content
                print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
            else:
                note = run_with_cleanup(
                    mcp_read_note.fn(
                        identifier=identifier,
                        project=project_name,
                        workspace=workspace,
                        page=page,
                        page_size=page_size,
                    )
                )
                if strip_frontmatter:
                    note, _ = _parse_opening_frontmatter(note)
                rprint(note)
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
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Edit an existing markdown note using append/prepend/find_replace/replace_section.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)
        _validate_edit_note_args(operation, find_text, section)

        # look for the project in the config
        config_manager = ConfigManager()
        project_name = None
        if project is not None:
            project_name, _ = config_manager.get_project(project)
            if not project_name:
                typer.echo(f"No project found named: {project}", err=True)
                raise typer.Exit(1)

        # use the project name, or the default from the config
        project_name = project_name or config_manager.default_project

        with force_routing(local=local, cloud=cloud):
            if format == "json":
                result = run_with_cleanup(
                    _edit_note_json(
                        identifier=identifier,
                        operation=operation,
                        content=content,
                        project_name=project_name,
                        workspace=workspace,
                        section=section,
                        find_text=find_text,
                        expected_replacements=expected_replacements,
                    )
                )
                print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
            else:
                result = run_with_cleanup(
                    mcp_edit_note.fn(
                        identifier=identifier,
                        operation=operation,
                        content=content,
                        project=project_name,
                        workspace=workspace,
                        section=section,
                        find_text=find_text,
                        expected_replacements=expected_replacements,
                    )
                )
                rprint(result)
                if _is_edit_note_failure_response(result):
                    raise typer.Exit(1)
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
    url: MemoryUrl,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
    format: str = typer.Option("json", "--format", help="Output format: text or json"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get context needed to continue a discussion.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)

        # look for the project in the config
        config_manager = ConfigManager()
        project_name = None
        if project is not None:
            project_name, _ = config_manager.get_project(project)
            if not project_name:
                typer.echo(f"No project found named: {project}", err=True)
                raise typer.Exit(1)

        # use the project name, or the default from the config
        project_name = project_name or config_manager.default_project

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                mcp_build_context.fn(
                    project=project_name,
                    workspace=workspace,
                    url=url,
                    depth=depth,
                    timeframe=timeframe,
                    page=page,
                    page_size=page_size,
                    max_related=max_related,
                    output_format="text" if format == "text" else "json",
                )
            )
        if format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        else:
            print(result)
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
    type: Annotated[Optional[List[SearchItemType]], typer.Option()] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = typer.Option(1, "--page", help="Page number for pagination (JSON format)"),
    page_size: int = typer.Option(
        50, "--page-size", help="Number of results per page (JSON format)"
    ),
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Get recent activity across the knowledge base.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)

        # Resolve project from config for JSON mode
        config_manager = ConfigManager()
        project_name = None
        if project is not None:
            project_name, _ = config_manager.get_project(project)
            if not project_name:
                typer.echo(f"No project found named: {project}", err=True)
                raise typer.Exit(1)
        project_name = project_name or config_manager.default_project

        with force_routing(local=local, cloud=cloud):
            if format == "json":
                result = run_with_cleanup(
                    _recent_activity_json(
                        type=type,
                        depth=depth,
                        timeframe=timeframe,
                        project_name=project_name,
                        workspace=workspace,
                        page=page,
                        page_size=page_size,
                    )
                )
                print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
            else:
                result = run_with_cleanup(
                    mcp_recent_activity.fn(
                        type=type,  # pyright: ignore [reportArgumentType]
                        depth=depth,
                        timeframe=timeframe,
                        project=project_name,
                        workspace=workspace,
                    )
                )
                # The tool returns a formatted string directly
                print(result)
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
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to use for the note. If not provided, the default project will be used."
        ),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option(help="Cloud workspace tenant ID or unique name to route this request."),
    ] = None,
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
    page: int = 1,
    page_size: int = 10,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Search across all content in the knowledge base.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)

        # look for the project in the config
        config_manager = ConfigManager()
        project_name = None
        if project is not None:
            project_name, _ = config_manager.get_project(project)
            if not project_name:
                typer.echo(f"No project found named: {project}", err=True)
                raise typer.Exit(1)

        # use the project name, or the default from the config
        project_name = project_name or config_manager.default_project

        mode_flags = [permalink, title, vector, hybrid]
        if sum(1 for enabled in mode_flags if enabled) > 1:  # pragma: no cover
            typer.echo(
                "Use only one mode flag: --permalink, --title, --vector, or --hybrid. Exiting.",
                err=True,
            )
            raise typer.Exit(1)

        # Build metadata filters from --filter and --meta
        metadata_filters = {}
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

        # set search type
        search_type = "text"
        if permalink:
            search_type = "permalink"
            if query and "*" in query:
                search_type = "permalink"
        if title:
            search_type = "title"
        if vector:
            search_type = "vector"
        if hybrid:
            search_type = "hybrid"

        with force_routing(local=local, cloud=cloud):
            results = run_with_cleanup(
                mcp_search.fn(
                    query=query or "",
                    project=project_name,
                    workspace=workspace,
                    search_type=search_type,
                    page=page,
                    after_date=after_date,
                    page_size=page_size,
                    types=note_types,
                    entity_types=entity_types,
                    metadata_filters=metadata_filters,
                    tags=tags,
                    status=status,
                )
            )
        if isinstance(results, str):
            print(results)
            raise typer.Exit(1)

        results_dict = results.model_dump(exclude_none=True)
        print(json.dumps(results_dict, indent=2, ensure_ascii=True, default=str))
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            logger.exception("Error during search", e)
            typer.echo(f"Error during search: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command(name="continue-conversation")
def continue_conversation(
    topic: Annotated[Optional[str], typer.Option(help="Topic or keyword to search for")] = None,
    timeframe: Annotated[
        Optional[str], typer.Option(help="How far back to look for activity")
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Prompt to continue a previous conversation or work session.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)

        with force_routing(local=local, cloud=cloud):
            # Prompt functions return formatted strings directly
            session = run_with_cleanup(
                mcp_continue_conversation.fn(topic=topic, timeframe=timeframe)  # type: ignore[arg-type]
            )
        rprint(session)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            logger.exception("Error continuing conversation", e)
            typer.echo(f"Error continuing conversation: {e}", err=True)
            raise typer.Exit(1)
        raise
