"""Read-only retrieval inspection commands."""

from typing import Annotated, Optional, assert_never

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from basic_memory.cli.app import app
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.cli.commands.tool import _resolve_output_mode, _validate_output_flags
from basic_memory.mcp.clients.inspect import InspectClient
from basic_memory.mcp.project_context import get_project_client
from basic_memory.schemas.inspect import (
    ChunkStatus,
    InspectFreshness,
    InspectChunksResponse,
    InspectDetachedSearchRow,
    InspectIndexBehindRowsDetail,
    InspectRowsBehindFileDetail,
    InspectSearchRow,
)

inspect_app = typer.Typer()
app.add_typer(inspect_app, name="inspect", help="Inspect retrieval projections")

console = Console()


async def run_inspect_chunks(
    identifier: str,
    *,
    project: str | None,
    project_id: str | None,
) -> InspectChunksResponse:
    """Resolve the project route and fetch one chunk inspection."""
    async with get_project_client(project=project, project_id=project_id) as (
        http_client,
        active_project,
    ):
        return await InspectClient(http_client, active_project.external_id).inspect_chunks(
            identifier
        )


def _text_preview(text: str, limit: int = 120) -> str:
    """Return a compact single-line chunk preview for human output."""
    single_line = " ".join(text.split())
    if len(single_line) <= limit:
        return single_line
    return f"{single_line[: limit - 3].rstrip()}..."


def _row_label(row: InspectSearchRow) -> str:
    """Build a compact search-row identity without hiding type-specific metadata."""
    details = [f"{row.type}:{row.id}"]
    if row.title:
        details.append(row.title)
    if row.category:
        details.append(f"category={row.category}")
    if row.relation_type:
        details.append(f"relation={row.relation_type}")
    return " · ".join(details)


def _detached_row_label(row: InspectDetachedSearchRow) -> str:
    """Build the stored identity for chunks whose source row disappeared."""
    return f"{row.type}:{row.id} · source row gone"


def _rich_status(status: ChunkStatus) -> Text:
    """Render every closed chunk status with a distinct terminal style."""
    match status:
        case "ready":
            return Text("ready", style="green")
        case "pending":
            return Text("pending", style="yellow")
        case "stale":
            return Text("stale", style="bold red")
        case "orphaned":
            return Text("orphaned", style="magenta")
        case unexpected:  # pragma: no cover - schema validation is exhaustive
            assert_never(unexpected)


def _rich_freshness(freshness: InspectFreshness) -> Text:
    """Render every closed freshness state with its diagnostic severity."""
    match freshness:
        case "fresh":
            return Text("fresh", style="green")
        case "not_indexed":
            return Text("not_indexed", style="yellow")
        case "index_behind_rows":
            return Text("index_behind_rows", style="yellow")
        case "rows_behind_file":
            return Text("rows_behind_file", style="red")
        case "unknown":
            return Text("unknown", style="dim")
        case unexpected:  # pragma: no cover - schema validation is exhaustive
            assert_never(unexpected)


def _display_value(value: str | list[str] | None) -> str:
    """Render optional and multi-valued diagnostic evidence compactly."""
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(value)
    return value


def _freshness_detail_lines(response: InspectChunksResponse) -> tuple[str, ...]:
    """Render the evidence required by each non-fresh state."""
    match response.freshness:
        case "fresh" | "not_indexed":
            return ()
        case "index_behind_rows":
            detail = response.freshness_detail
            if not isinstance(detail, InspectIndexBehindRowsDetail):  # pragma: no cover
                raise ValueError("index_behind_rows requires fingerprint detail")
            return (
                f"Indexed fingerprint: {_display_value(detail.entity_fingerprint_indexed)}",
                f"Current fingerprint: {detail.entity_fingerprint_current}",
                f"Current chunks missing from manifest: {detail.missing_chunk_count}",
            )
        case "rows_behind_file" | "unknown":
            detail = response.freshness_detail
            if not isinstance(detail, InspectRowsBehindFileDetail):  # pragma: no cover
                raise ValueError(f"{response.freshness} requires file lineage detail")
            return (
                f"Entity checksum: {_display_value(detail.entity_checksum)}",
                f"Current file checksum: {_display_value(detail.current_file_checksum)}",
                f"DB checksum: {_display_value(detail.db_checksum)}",
                f"Lineage file checksum: {_display_value(detail.file_checksum)}",
                f"File write status: {_display_value(detail.file_write_status)}",
            )
        case unexpected:  # pragma: no cover - InspectFreshness is exhaustive
            assert_never(unexpected)


def _display_chunks(response: InspectChunksResponse) -> None:
    """Render a Rich identity header and one chunk table per search row."""
    readiness = response.readiness
    if response.entity_fingerprint_indexed is None:
        fingerprint_match = "not indexed"
    elif response.stale:
        fingerprint_match = "no"
    else:
        fingerprint_match = "yes"

    header = Text()
    header.append(f"{response.title}\n", style="bold cyan")
    header.append(f"{response.file_path}")
    if response.permalink:
        header.append(f"  ·  {response.permalink}", style="green")
    header.append(f"\nEntity: {response.entity_id}  ·  {response.external_id}")
    header.append(
        f"\nEngine: {response.configured_vector_index}  ·  {response.configured_embedding_model}"
    )
    header.append(
        "\nReadiness: "
        f"{readiness.ready} ready, {readiness.pending} pending, "
        f"{readiness.stale} stale, {readiness.orphaned} orphaned, "
        f"{readiness.missing} missing ({readiness.total} total)"
    )
    header.append(f"\nFingerprint match: {fingerprint_match}")
    header.append("\nFreshness: ")
    header.append_text(_rich_freshness(response.freshness))
    for line in _freshness_detail_lines(response):
        header.append(f"\n{line}", style="dim")
    console.print(Panel(header, title="Retrieval chunks", expand=False))

    if readiness.total == 0:
        console.print(
            "[yellow]No vector chunks are stored; showing search rows only. "
            "Semantic search may be disabled.[/yellow]"
        )

    for row in response.rows:
        table = Table(title=Text(_row_label(row)), show_header=True, header_style="bold")
        table.add_column("Ordinal", justify="right")
        table.add_column("Status")
        table.add_column("Chars", justify="right")
        table.add_column("Text preview", max_width=80)
        for chunk in row.chunks:
            table.add_row(
                str(chunk.ordinal),
                _rich_status(chunk.status),
                str(len(chunk.text)),
                Text(_text_preview(chunk.text)),
            )
        console.print(table)

    for row in response.detached:
        table = Table(
            title=Text(_detached_row_label(row), style="bold red"),
            show_header=True,
            header_style="bold",
        )
        table.add_column("Ordinal", justify="right")
        table.add_column("Status")
        table.add_column("Chars", justify="right")
        table.add_column("Text preview", max_width=80)
        for chunk in row.chunks:
            table.add_row(
                str(chunk.ordinal),
                _rich_status(chunk.status),
                str(len(chunk.text)),
                Text(_text_preview(chunk.text)),
            )
        console.print(table)


def _plain_chunks(response: InspectChunksResponse) -> None:
    """Render the same inspection as undecorated, greppable text."""
    readiness = response.readiness
    if response.entity_fingerprint_indexed is None:
        fingerprint_match = "not indexed"
    else:
        fingerprint_match = "no" if response.stale else "yes"

    typer.echo(f"Retrieval chunks: {response.title}")
    typer.echo(f"Path: {response.file_path}")
    typer.echo(f"Permalink: {response.permalink or '-'}")
    typer.echo(f"Entity: {response.entity_id} ({response.external_id})")
    typer.echo(
        f"Engine: {response.configured_vector_index} / {response.configured_embedding_model}"
    )
    typer.echo(
        "Readiness: "
        f"ready={readiness.ready} pending={readiness.pending} stale={readiness.stale} "
        f"orphaned={readiness.orphaned} missing={readiness.missing} total={readiness.total}"
    )
    typer.echo(f"Fingerprint match: {fingerprint_match}")
    typer.echo(f"Freshness: {response.freshness}")
    for line in _freshness_detail_lines(response):
        typer.echo(line)

    if readiness.total == 0:
        typer.echo(
            "Note: No vector chunks are stored; showing search rows only. "
            "Semantic search may be disabled."
        )

    for row in response.rows:
        typer.echo(f"\n{_row_label(row)}")
        if not row.chunks:
            typer.echo("  (no chunks)")
            continue
        for chunk in row.chunks:
            typer.echo(
                f"  {chunk.ordinal}  {chunk.status}  {len(chunk.text)} chars  "
                f"{_text_preview(chunk.text)}"
            )

    for row in response.detached:
        typer.echo(f"\n{_detached_row_label(row)}")
        for chunk in row.chunks:
            typer.echo(
                f"  {chunk.ordinal}  {chunk.status}  {len(chunk.text)} chars  "
                f"{_text_preview(chunk.text)}"
            )


@inspect_app.command("chunks")
def inspect_chunks(
    identifier: Annotated[str, typer.Argument(help="Note identifier to inspect")],
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    plain: bool = typer.Option(False, "--plain", help="Output undecorated plain text"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use; defaults to the configured project."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID); takes precedence over --project.",
        ),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Show how the retrieval index decomposes one note into rows and chunks."""
    from basic_memory.cli.commands.command_utils import run_with_cleanup
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)
        with force_routing(local=local, cloud=cloud):
            response = run_with_cleanup(
                run_inspect_chunks(
                    identifier,
                    project=project,
                    project_id=project_id,
                )
            )

        mode = _resolve_output_mode(json_output, plain)
        if mode == "json":
            print(response.model_dump_json(indent=2))
        elif mode == "plain":
            _plain_chunks(response)
        else:
            _display_chunks(response)
    except typer.Exit:
        raise
    except (ToolError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except Exception as exc:  # pragma: no cover
        logger.error(f"Error inspecting retrieval chunks: {exc}")
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
