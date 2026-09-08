"""Import command: extract one document file in a project into a sidecar Markdown note."""

# PEP 563 lazy annotations keep the ingestion stack out of module import (#886).
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel

from basic_memory.cli.app import import_app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing
from basic_memory.config import ConfigManager

if TYPE_CHECKING:
    from basic_memory.document_ingestion.raw_document import RawDocumentWriteResult

console = Console()


async def import_document(path: Path, project: str | None) -> tuple[str, RawDocumentWriteResult]:
    """Index the source, extract it, and write its sidecar note into the project."""
    # Deferred: the ingestion stack and API client load only when the command runs (#886).
    from basic_memory.document_ingestion.local_runtime import (
        ApiDocumentSourceEntityResolver,
        LocalDocumentSourceReader,
        LocalRawDocumentWriter,
        default_document_extractors,
    )
    from basic_memory.document_ingestion.raw_document import RawDocumentRuntime
    from basic_memory.markdown import EntityParser, MarkdownProcessor
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import KnowledgeClient, ProjectClient
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.services.file_service import FileService

    config_manager = ConfigManager()
    project_name = project or config_manager.default_project
    async with get_client(project_name=project_name) as client:
        project_item = await get_active_project(client, project_name, None)
        project_home = Path(project_item.path).expanduser().resolve()
        source = path.expanduser().resolve()
        if not source.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        if not source.is_relative_to(project_home):
            raise typer.BadParameter(
                f"{path} is not inside project {project_item.name!r} ({project_home})"
            )
        relative_path = source.relative_to(project_home).as_posix()

        # The source must be an indexed file entity before it can own a document
        # note, and a fresh drop may not have been seen by the watcher yet.
        await ProjectClient(client).index(
            project_item.external_id, force_full=False, run_in_background=False
        )

        knowledge = KnowledgeClient(client, project_item.external_id)
        app_config = config_manager.config
        markdown_processor = MarkdownProcessor(EntityParser(project_home), app_config=app_config)
        file_service = FileService(project_home, markdown_processor, app_config=app_config)
        runtime = RawDocumentRuntime(
            source_resolver=ApiDocumentSourceEntityResolver(knowledge),
            source_reader=LocalDocumentSourceReader(project_home),
            extractors=default_document_extractors(),
            writer=LocalRawDocumentWriter(file_service, knowledge),
        )
        result = await runtime.ingest(file_path=relative_path, observed_etag=None)
        return project_item.name, result


@import_app.command(
    name="document",
    help="Extract a PDF, docx, pptx, or csv file in a project into a sidecar Markdown note.",
)
def document(
    path: Annotated[Path, typer.Argument(help="Path to a file inside the project directory")],
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project name (defaults to the default project)"),
    ] = None,
) -> None:
    """Write ``<file>.md`` next to the source and a run note under document-ingestion-runs/."""
    try:
        # Trigger: the project may be configured for cloud routing.
        # Why: this runtime reads the source and writes the sidecar in the local
        #      project directory, so the API it indexes through must be the local
        #      one; a cloud client would be asked to index files it cannot see.
        # Outcome: local ASGI routing for the whole command, whatever the project mode.
        with force_routing(local=True):
            project_name, result = run_with_cleanup(import_document(path, project))
    except typer.BadParameter as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1)
    except Exception as error:
        logger.error("Document import failed")
        typer.echo(f"Error during import: {error}", err=True)
        raise typer.Exit(1)

    document_state = "created" if result.document_created else "already current"
    run_state = "created" if result.run_created else "already recorded"
    console.print(
        Panel(
            f"[green]Document import complete![/green]\n\n"
            f"Project: {project_name}\n"
            f"Document note: {result.document_file_path} ({document_state})\n"
            f"Run note: {result.run_file_path} ({run_state})\n"
            f"Run id: {result.run_id}",
            expand=False,
        )
    )
