"""CLI tool commands for Basic Memory."""

import json
import sys
from typing import Annotated, List, Optional

import typer
from loguru import logger
from rich import print as rprint

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager

# Import prompts
from basic_memory.mcp.prompts.continue_conversation import (
    continue_conversation as mcp_continue_conversation,
)
from basic_memory.mcp.prompts.recent_activity import (
    recent_activity_prompt as recent_activity_prompt,
)
from basic_memory.mcp.tools import build_context as mcp_build_context
from basic_memory.mcp.tools import read_note as mcp_read_note
from basic_memory.mcp.tools import recent_activity as mcp_recent_activity
from basic_memory.mcp.tools import search_notes as mcp_search
from basic_memory.mcp.tools import write_note as mcp_write_note
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import MemoryUrl
from basic_memory.schemas.search import SearchItemType

tool_app = typer.Typer()
app.add_typer(tool_app, name="tool", help="Access to MCP tools via CLI")


# --- JSON Format Helpers ---
# These helpers provide JSON output for OpenClaw plugin integration
# They call the API directly to avoid double round-trips (see issue #553)


async def _write_note_json(
    title: str,
    content: str | None,
    directory: str,
    project_name: str | None,
    tags: list[str] | None,
) -> dict:
    """Write a note and return JSON response with entity metadata.

    Calls the API directly to avoid the double round-trip issue where
    the MCP tool writes via one API call, then we'd need another to
    get the structured data back.
    """
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import KnowledgeClient
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.base import Entity
    from basic_memory.utils import parse_tags

    async with get_client() as client:
        # Get active project
        active_project = await get_active_project(client, project_name, None)

        # Normalize directory
        if directory == "/":
            directory = ""

        # Process tags
        tag_list = parse_tags(tags)
        metadata = {"tags": tag_list} if tag_list else None

        # Create entity
        entity = Entity(
            title=title,
            directory=directory,
            entity_type="note",
            content_type="text/markdown",
            content=content or "",
            entity_metadata=metadata,
        )

        # Use typed client
        knowledge_client = KnowledgeClient(client, active_project.external_id)

        # Try create first (optimistic)
        try:
            result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
        except Exception as e:
            # If conflict, try update
            if "409" in str(e) or "conflict" in str(e).lower() or "already exists" in str(e).lower():
                if not entity.permalink:
                    raise ValueError("Entity permalink required for updates")
                entity_id = await knowledge_client.resolve_entity(entity.permalink)
                result = await knowledge_client.update_entity(
                    entity_id, entity.model_dump(), fast=False
                )
            else:
                raise

        # Return the entity data as dict
        return result


async def _read_note_json(identifier: str, project_name: str | None, page: int, page_size: int) -> dict:
    """Read a note and return JSON response.

    Handles plain titles, permalinks, and memory:// URLs via memory_url_path().
    """
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import KnowledgeClient
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.memory import memory_url_path

    async with get_client() as client:
        # Get active project
        active_project = await get_active_project(client, project_name, None)

        # Process identifier (handles memory://, permalinks, plain titles)
        entity_path = memory_url_path(identifier)

        # Use typed client
        knowledge_client = KnowledgeClient(client, active_project.external_id)

        # Resolve and fetch
        entity_id = await knowledge_client.resolve_entity(entity_path)
        entity_data = await knowledge_client.get_entity(entity_id)

        return entity_data


async def _recent_activity_json(
    type: list[SearchItemType] | None,
    depth: int,
    timeframe: TimeFrame,
    project: str | None,
    page: int,
    page_size: int,
) -> dict:
    """Get recent activity and return JSON response.

    Now supports pagination via page and page_size parameters (issue #553).
    """
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.mcp.tools.utils import call_get
    from basic_memory.schemas.memory import GraphContext

    async with get_client() as client:
        # Get active project
        active_project = await get_active_project(client, project, None)

        # Build params
        params = {
            "page": page,
            "page_size": page_size,
            "max_related": 10,
        }
        if depth:
            params["depth"] = depth
        if timeframe:
            params["timeframe"] = timeframe  # type: ignore
        if type:
            params["type"] = [t.value for t in type]  # type: ignore

        # Call API
        response = await call_get(
            client,
            f"/v2/projects/{active_project.external_id}/memory/recent",
            params=params,
        )

        # Return as dict
        activity_data = GraphContext.model_validate(response.json())
        return activity_data.model_dump(exclude_none=True)


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
    content: Annotated[
        Optional[str],
        typer.Option(
            help="The content of the note. If not provided, content will be read from stdin. This allows piping content from other commands, e.g.: cat file.md | basic-memory tools write-note"
        ),
    ] = None,
    tags: Annotated[
        Optional[List[str]], typer.Option(help="A list of tags to apply to the note")
    ] = None,
    format: Annotated[
        Optional[str],
        typer.Option(help="Output format: 'text' (default) or 'json'"),
    ] = None,
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

        with force_routing(local=local, cloud=cloud):
            if format == "json":
                # For JSON output, bypass the MCP tool and call the API directly
                # to avoid the double round-trip mentioned in issue #553
                result = run_with_cleanup(_write_note_json(title, content, folder, project_name, tags))
                print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
            else:
                result = run_with_cleanup(mcp_write_note.fn(title, content, folder, project_name, tags))
                rprint(result)
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
    page: int = 1,
    page_size: int = 10,
    format: Annotated[
        Optional[str],
        typer.Option(help="Output format: 'text' (default) or 'json'"),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Read a markdown note from the knowledge base.

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
            if format == "json":
                # For JSON output, use helper that returns structured data
                result = run_with_cleanup(_read_note_json(identifier, project_name, page, page_size))
                print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
            else:
                result = run_with_cleanup(mcp_read_note.fn(identifier, project_name, page, page_size))
                rprint(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during read_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def build_context(
    url: MemoryUrl,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
    format: Annotated[
        Optional[str],
        typer.Option(help="Output format: 'json' (always outputs JSON, flag added for consistency)"),
    ] = None,
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
            context = run_with_cleanup(
                mcp_build_context.fn(
                    project=project_name,
                    url=url,
                    depth=depth,
                    timeframe=timeframe,
                    page=page,
                    page_size=page_size,
                    max_related=max_related,
                )
            )
        # Use json module for more controlled serialization
        import json

        context_dict = context.model_dump(exclude_none=True)
        print(json.dumps(context_dict, indent=2, ensure_ascii=True, default=str))
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
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to use. If not provided, the default project will be used."
        ),
    ] = None,
    page: int = 1,
    page_size: int = 50,
    format: Annotated[
        Optional[str],
        typer.Option(help="Output format: 'text' (default) or 'json'"),
    ] = None,
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
                # For JSON output, use helper with pagination support
                result = run_with_cleanup(
                    _recent_activity_json(
                        type=type,  # pyright: ignore [reportArgumentType]
                        depth=depth or 1,
                        timeframe=timeframe or "7d",
                        project=project_name,
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
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to use for the note. If not provided, the default project will be used."
        ),
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

        if permalink and title:  # pragma: no cover
            typer.echo(
                "Use either --permalink or --title, not both. Exiting.",
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

        with force_routing(local=local, cloud=cloud):
            results = run_with_cleanup(
                mcp_search.fn(
                    query or "",
                    project_name,
                    search_type=search_type,
                    page=page,
                    after_date=after_date,
                    page_size=page_size,
                    types=note_types,
                    metadata_filters=metadata_filters,
                    tags=tags,
                    status=status,
                )
            )
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


# @tool_app.command(name="show-recent-activity")
# def show_recent_activity(
#     timeframe: Annotated[
#         str, typer.Option(help="How far back to look for activity")
#     ] = "7d",
# ):
#     """Prompt to show recent activity."""
#     try:
#         # Prompt functions return formatted strings directly
#         session = asyncio.run(recent_activity_prompt(timeframe=timeframe))
#         rprint(session)
#     except Exception as e:  # pragma: no cover
#         if not isinstance(e, typer.Exit):
#             logger.exception("Error continuing conversation", e)
#             typer.echo(f"Error continuing conversation: {e}", err=True)
#             raise typer.Exit(1)
#         raise
