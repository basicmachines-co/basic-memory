"""CLI tool commands for Basic Memory."""

import asyncio
from typing import Optional, List, Annotated

import typer
from loguru import logger
from rich import print as rprint

from basic_memory.cli.app import app
from basic_memory.mcp.tools import build_context as mcp_build_context
from basic_memory.mcp.tools import get_entity as mcp_get_entity
from basic_memory.mcp.tools import read_note as mcp_read_note
from basic_memory.mcp.tools import recent_activity as mcp_recent_activity
from basic_memory.mcp.tools import search as mcp_search
from basic_memory.mcp.tools import write_note as mcp_write_note

# Import prompts
from basic_memory.mcp.prompts.continue_conversation import (
    continue_conversation as mcp_continue_conversation,
)

from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import MemoryUrl
from basic_memory.schemas.search import SearchQuery, SearchItemType

tool_app = typer.Typer()
app.add_typer(tool_app, name="tools", help="cli versions mcp tools")


@tool_app.command()
def write_note(
    title: Annotated[str, typer.Option(help="The title of the note")],
    content: Annotated[str, typer.Option(help="The content of the note")],
    folder: Annotated[str, typer.Option(help="The folder to create the note in")],
    tags: Annotated[
        Optional[List[str]], typer.Option(help="A list of tags to apply to the note")
    ] = None,
):
    try:
        note = asyncio.run(mcp_write_note(title, content, folder, tags))
        rprint(note)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during write_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def read_note(identifier: str, page: int = 1, page_size: int = 10):
    try:
        note = asyncio.run(mcp_read_note(identifier, page, page_size))
        rprint(note)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during read_note: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def build_context(
    url: MemoryUrl,
    depth: Optional[int] = 1,
    timeframe: Optional[TimeFrame] = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
):
    try:
        context = asyncio.run(
            mcp_build_context(
                url=url,
                depth=depth,
                timeframe=timeframe,
                page=page,
                page_size=page_size,
                max_related=max_related,
            )
        )
        rprint(context.model_dump_json(indent=2))
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
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
):
    try:
        context = asyncio.run(
            mcp_recent_activity(
                type=type,  # pyright: ignore [reportArgumentType]
                depth=depth,
                timeframe=timeframe,
                page=page,
                page_size=page_size,
                max_related=max_related,
            )
        )
        rprint(context.model_dump_json(indent=2))
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during build_context: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def search(
    query: str,
    permalink: Annotated[bool, typer.Option("--permalink", help="Search permalink values")] = False,
    title: Annotated[bool, typer.Option("--title", help="Search title values")] = False,
    after_date: Annotated[
        Optional[str],
        typer.Option("--after_date", help="Search results after date, eg. '2d', '1 week'"),
    ] = None,
    page: int = 1,
    page_size: int = 10,
):
    if permalink and title:  # pragma: no cover
        print("Cannot search both permalink and title")
        raise typer.Abort()

    try:
        search_query = SearchQuery(
            permalink_match=query if permalink else None,
            text=query if not (permalink or title) else None,
            title=query if title else None,
            after_date=after_date,
        )
        results = asyncio.run(mcp_search(query=search_query, page=page, page_size=page_size))
        rprint(results.model_dump_json(indent=2))
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            logger.exception("Error during search", e)
            typer.echo(f"Error during search: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command()
def get_entity(identifier: str):
    try:
        entity = asyncio.run(mcp_get_entity(identifier=identifier))
        rprint(entity.model_dump_json(indent=2))
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error during get_entity: {e}", err=True)
            raise typer.Exit(1)
        raise


@tool_app.command(name="continue-conversation")
def continue_conversation(
    topic: Annotated[Optional[str], typer.Option(help="Topic or keyword to search for")] = None,
    timeframe: Annotated[
        Optional[str], typer.Option(help="How far back to look for activity")
    ] = None,
):
    """Continue a previous conversation or work session."""
    try:
        # Prompt functions return formatted strings directly
        session = asyncio.run(mcp_continue_conversation(topic=topic, timeframe=timeframe))
        rprint(session)
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            logger.exception("Error continuing conversation", e)
            typer.echo(f"Error continuing conversation: {e}", err=True)
            raise typer.Exit(1)
        raise
