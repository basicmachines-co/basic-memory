"""CLI tool commands for Basic Memory."""

import json
import sys
from typing import Annotated, List, Optional

import typer
from loguru import logger
from rich import print as rprint

from basic_memory.cli.app import app
from basic_memory.cli.commands.tool_utils import resolve_project, run_async_tool

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

    project_name = resolve_project(project)
    note = run_async_tool(mcp_write_note.fn, title, content, folder, project_name, tags)
    rprint(note)


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
    project_name = resolve_project(project)
    note = run_async_tool(mcp_read_note.fn, identifier, project_name, page, page_size)
    rprint(note)


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
    project_name = resolve_project(project)
    context = run_async_tool(
        mcp_build_context.fn,
        project=project_name,
        url=url,
        depth=depth,
        timeframe=timeframe,
        page=page,
        page_size=page_size,
        max_related=max_related,
    )
    context_dict = context.model_dump(exclude_none=True)
    print(json.dumps(context_dict, indent=2, ensure_ascii=True, default=str))


@tool_app.command()
def recent_activity(
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to use. If not provided, the default project will be used."
        ),
    ] = None,
    type: Annotated[Optional[List[SearchItemType]], typer.Option()] = None,
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
):
    """Get recent activity across the knowledge base."""
    project_name = resolve_project(project)
    result = run_async_tool(
        mcp_recent_activity.fn,
        project=project_name,
        type=type,  # pyright: ignore [reportArgumentType]
        depth=depth,
        timeframe=timeframe,
    )
    # The tool now returns a formatted string directly
    print(result)


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
    if permalink and title:
        typer.echo(
            "Use either --permalink or --title, not both. Exiting.",
            err=True,
        )
        raise typer.Exit(1)

    # Determine search type (fixed logic - was overwriting itself before)
    if permalink:
        search_type = "permalink_match" if "*" in query else "permalink"
    elif title:
        search_type = "title"
    else:
        search_type = "text"

    project_name = resolve_project(project)
    results = run_async_tool(
        mcp_search.fn,
        query,
        project_name,
        search_type=search_type,
        page=page,
        after_date=after_date,
        page_size=page_size,
    )
    results_dict = results.model_dump(exclude_none=True)
    print(json.dumps(results_dict, indent=2, ensure_ascii=True, default=str))


@tool_app.command(name="continue-conversation")
def continue_conversation(
    topic: Annotated[Optional[str], typer.Option(help="Topic or keyword to search for")] = None,
    timeframe: Annotated[
        Optional[str], typer.Option(help="How far back to look for activity")
    ] = None,
):
    """Prompt to continue a previous conversation or work session."""
    session = run_async_tool(mcp_continue_conversation.fn, topic=topic, timeframe=timeframe)  # type: ignore
    rprint(session)


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
