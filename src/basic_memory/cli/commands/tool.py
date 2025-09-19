"""CLI tool commands for Basic Memory."""

import asyncio
import sys
from typing import Annotated, List, Optional

import typer
from loguru import logger
from rich import print as rprint

from basic_memory.cli.app import app
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
):
    """Create or update a markdown note. Content can be provided as an argument or read from stdin.

    Content can be provided in two ways:
    1. Using the --content parameter
    2. Piping content through stdin (if --content is not provided)

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
    """
    try:
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

        note = asyncio.run(mcp_write_note.fn(title, content, folder, project_name, tags))
        rprint(note)
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
):
    """Read a markdown note from the knowledge base."""

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

    try:
        note = asyncio.run(mcp_read_note.fn(identifier, project_name, page, page_size))
        rprint(note)
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
):
    """Get context needed to continue a discussion."""

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

    try:
        context = asyncio.run(
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
):
    """Get recent activity across the knowledge base."""
    try:
        result = asyncio.run(
            mcp_recent_activity.fn(
                type=type,  # pyright: ignore [reportArgumentType]
                depth=depth,
                timeframe=timeframe,
            )
        )
        # The tool now returns a formatted string directly
        print(result)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during recent_activity: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command("search-notes")
def search_notes(
    query: str,
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
    page: int = 1,
    page_size: int = 10,
):
    """Search across all content in the knowledge base."""

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
        print("Cannot search both permalink and title")
        raise typer.Abort()

    try:
        if permalink and title:  # pragma: no cover
            typer.echo(
                "Use either --permalink or --title, not both. Exiting.",
                err=True,
            )
            raise typer.Exit(1)

        # set search type
        search_type = ("permalink" if permalink else None,)
        search_type = ("permalink_match" if permalink and "*" in query else None,)
        search_type = ("title" if title else None,)
        search_type = "text" if search_type is None else search_type

        results = asyncio.run(
            mcp_search.fn(
                query,
                project_name,
                search_type=search_type,
                page=page,
                after_date=after_date,
                page_size=page_size,
            )
        )
        # Use json module for more controlled serialization
        import json

        results_dict = results.model_dump(exclude_none=True)
        print(json.dumps(results_dict, indent=2, ensure_ascii=True, default=str))
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
):
    """Prompt to continue a previous conversation or work session."""
    try:
        # Prompt functions return formatted strings directly
        session = asyncio.run(mcp_continue_conversation.fn(topic=topic, timeframe=timeframe))  # type: ignore
        rprint(session)
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
