"""Read-only retrieval inspection commands."""

from enum import Enum
from itertools import groupby
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
    InspectQueryCandidate,
    InspectQueryResponse,
    InspectRowsBehindFileDetail,
    InspectSearchRow,
)
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode

inspect_app = typer.Typer()
app.add_typer(inspect_app, name="inspect", help="Inspect retrieval projections")

console = Console()


class InspectQueryMode(str, Enum):
    TEXT = "text"
    VECTOR = "vector"
    HYBRID = "hybrid"


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


async def run_inspect_query(
    query: SearchQuery,
    *,
    limit: int,
    offset: int,
    project: str | None,
    project_id: str | None,
) -> InspectQueryResponse:
    """Resolve the project route and execute one traced query."""
    async with get_project_client(project=project, project_id=project_id) as (
        http_client,
        active_project,
    ):
        return await InspectClient(http_client, active_project.external_id).inspect_query(
            query,
            limit=limit,
            offset=offset,
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


def _score_text(candidate: InspectQueryCandidate) -> str:
    score = candidate.scores.final_score
    return f"{score:.4f}" if score is not None else "-"


def _movement_text(candidate: InspectQueryCandidate) -> str:
    before = candidate.scores.pre_rerank_rank
    after = candidate.scores.post_rerank_rank
    if before is None or after is None:
        return "-"
    movement = before - after
    return f"{movement:+d}"


def _query_candidate_label(candidate: InspectQueryCandidate) -> str:
    if candidate.title or candidate.permalink:
        return candidate.title or candidate.permalink or ""
    if candidate.type is not None and candidate.id is not None:
        return f"{candidate.type}:{candidate.id}"
    if candidate.rejection_detail is not None and candidate.rejection_detail.chunk_key:
        return candidate.rejection_detail.chunk_key
    return "unknown candidate"


def _query_candidate_id(candidate: InspectQueryCandidate) -> str | None:
    if candidate.external_id is not None:
        return candidate.external_id
    if candidate.type is not None and candidate.id is not None:
        return f"{candidate.type}:{candidate.id}"
    return None


def _display_query(
    response: InspectQueryResponse,
    *,
    show_misses: bool,
    show_ids: bool,
) -> None:
    """Render the traced query as a compact human-readable query plan."""
    header = Text()
    header.append(f"{response.query}\n", style="bold cyan")
    header.append(f"Mode: {response.retrieval_mode.value}")
    header.append(f"  ·  Project: {response.project_id}")
    header.append(
        f"  ·  Window: {response.window.offset + 1}-"
        f"{response.window.offset + response.window.limit}"
    )
    console.print(Panel(header, title="Retrieval query", expand=False))

    engine = response.engine
    reranker = engine.reranker
    engine_text = Text()
    engine_text.append(f"Vector index: {engine.vector_index}\n")
    engine_text.append(f"Embedding model: {engine.embedding_model}\n")
    if engine.ready_rows is None:
        engine_text.append("Readiness: n/a (FTS execution reads no vector manifest)\n")
    else:
        engine_text.append(
            "Readiness: "
            f"{engine.ready_rows} ready, {engine.pending_rows} pending, "
            f"{engine.other_identity_rows} other identity\n"
        )
    engine_text.append(f"Fusion: {engine.fusion_formula}\n")
    engine_text.append(
        f"Minimum similarity: {engine.min_similarity:.4f} ({engine.min_similarity_source})\n"
    )
    if reranker.enabled:
        status = "applied" if reranker.applied else f"skipped: {reranker.skipped_reason}"
        engine_text.append(
            f"Reranker: {reranker.model} · {reranker.candidates} candidates · {status}"
        )
    else:
        engine_text.append("Reranker: disabled")
    console.print(Panel(engine_text, title="Engine", expand=False))

    stage_table = Table(title="Stages", show_header=True, header_style="bold")
    stage_table.add_column("Stage")
    stage_table.add_column("In", justify="right")
    stage_table.add_column("Out", justify="right")
    stage_table.add_column("Dropped", justify="right")
    stage_table.add_column("ms", justify="right")
    for stage in response.stages:
        stage_name = stage.name
        if stage.relaxed_fallback_used:
            stage_name = f"{stage.name} (relaxed fallback)"
        stage_table.add_row(
            stage_name,
            str(stage.count_in),
            str(stage.count_out),
            str(stage.dropped) if stage.dropped is not None else "-",
            f"{stage.ms:.2f}" if stage.ms is not None else "-",
        )
    console.print(stage_table)

    returned = [
        candidate for candidate in response.candidates if candidate.disposition == "returned"
    ]
    result_table = Table(title="Ranked results", show_header=True, header_style="bold")
    result_table.add_column("Rank", justify="right")
    result_table.add_column("Result")
    result_table.add_column("Score", justify="right")
    result_table.add_column("Δ", justify="right")
    for candidate in returned:
        label = _query_candidate_label(candidate)
        result = Text(label)
        if candidate.type == "entity" and candidate.permalink:
            result.append(f"\n{candidate.permalink}", style="green")
        if show_ids and (candidate_id := _query_candidate_id(candidate)) is not None:
            result.append(f"\n{candidate_id}", style="dim")
        result_table.add_row(
            str(candidate.scores.final_rank or "-"),
            result,
            _score_text(candidate),
            _movement_text(candidate),
        )
    console.print(result_table)

    if not show_misses:
        return
    if response.retrieval_mode == SearchRetrievalMode.FTS:
        console.print("[yellow]show-misses not applicable: FTS window is the SQL LIMIT[/yellow]")
        return

    console.print("[dim]Misses: bounded window — not exhaustive[/dim]")
    misses = sorted(
        (candidate for candidate in response.candidates if candidate.disposition != "returned"),
        key=lambda candidate: (
            candidate.disposition,
            candidate.type or "",
            candidate.id if candidate.id is not None else -1,
            _query_candidate_label(candidate),
        ),
    )
    for disposition, grouped in groupby(misses, key=lambda candidate: candidate.disposition):
        miss_table = Table(title=disposition, show_header=True, header_style="bold")
        miss_table.add_column("Candidate")
        miss_table.add_column("Score", justify="right")
        miss_table.add_column("Detail")
        for candidate in grouped:
            detail = candidate.rejection_detail
            detail_text = detail.model_dump_json(exclude_none=True) if detail is not None else "-"
            miss_table.add_row(
                _query_candidate_label(candidate),
                (
                    f"{candidate.scores.vector_similarity:.4f}"
                    if candidate.scores.vector_similarity is not None
                    else "-"
                ),
                detail_text,
            )
        console.print(miss_table)


def _plain_query(
    response: InspectQueryResponse,
    *,
    show_misses: bool,
    show_ids: bool,
) -> None:
    """Render the same query plan as undecorated, greppable text."""
    typer.echo(f"Retrieval query: {response.query}")
    typer.echo(f"Mode: {response.retrieval_mode.value}")
    typer.echo(f"Project: {response.project_id}")
    typer.echo("Engine:")
    typer.echo(f"  Vector index: {response.engine.vector_index}")
    typer.echo(f"  Embedding model: {response.engine.embedding_model}")
    if response.engine.ready_rows is None:
        typer.echo("  Readiness: n/a (FTS execution reads no vector manifest)")
    else:
        typer.echo(
            "  Readiness: "
            f"ready={response.engine.ready_rows} pending={response.engine.pending_rows} "
            f"other_identity={response.engine.other_identity_rows}"
        )
    typer.echo(f"  Fusion: {response.engine.fusion_formula}")
    reranker = response.engine.reranker
    if reranker.enabled:
        status = "applied" if reranker.applied else f"skipped={reranker.skipped_reason}"
        typer.echo(f"  Reranker: {reranker.model} candidates={reranker.candidates} {status}")
    else:
        typer.echo("  Reranker: disabled")

    typer.echo("Stages:")
    for stage in response.stages:
        ms = f"{stage.ms:.2f}" if stage.ms is not None else "-"
        relaxed = " relaxed_fallback=yes" if stage.relaxed_fallback_used else ""
        typer.echo(
            f"  {stage.name} in={stage.count_in} out={stage.count_out} "
            f"dropped={stage.dropped if stage.dropped is not None else '-'} ms={ms}{relaxed}"
        )

    typer.echo("Ranked results:")
    returned = [
        candidate for candidate in response.candidates if candidate.disposition == "returned"
    ]
    for candidate in returned:
        label = _query_candidate_label(candidate)
        identity = []
        if candidate.type == "entity" and candidate.permalink:
            identity.append(f"permalink={candidate.permalink}")
        if show_ids and (candidate_id := _query_candidate_id(candidate)) is not None:
            identity.append(f"id={candidate_id}")
        identity_text = f"  {' '.join(identity)}" if identity else ""
        typer.echo(
            f"  {candidate.scores.final_rank or '-'}  {_score_text(candidate)}  "
            f"delta={_movement_text(candidate)}  {label}{identity_text}"
        )

    if not show_misses:
        return
    if response.retrieval_mode == SearchRetrievalMode.FTS:
        typer.echo("show-misses not applicable: FTS window is the SQL LIMIT")
        return
    typer.echo("Misses (bounded window — not exhaustive):")
    misses = sorted(
        (candidate for candidate in response.candidates if candidate.disposition != "returned"),
        key=lambda candidate: (
            candidate.disposition,
            candidate.type or "",
            candidate.id if candidate.id is not None else -1,
            _query_candidate_label(candidate),
        ),
    )
    for disposition, grouped in groupby(misses, key=lambda candidate: candidate.disposition):
        typer.echo(f"  {disposition}:")
        for candidate in grouped:
            score = candidate.scores.vector_similarity
            score_text = f"{score:.4f}" if score is not None else "-"
            detail = candidate.rejection_detail
            detail_text = detail.model_dump_json(exclude_none=True) if detail is not None else "-"
            typer.echo(
                f"    {_query_candidate_label(candidate)} score={score_text} detail={detail_text}"
            )


@inspect_app.command("query")
def inspect_query(
    query_text: Annotated[str, typer.Argument(help="Text query to inspect")],
    mode: InspectQueryMode = typer.Option(
        InspectQueryMode.TEXT,
        "--mode",
        help="Retrieval mode: text, vector, or hybrid",
    ),
    show_misses: bool = typer.Option(
        False,
        "--show-misses",
        help="Render bounded rejected candidates in human output",
    ),
    show_ids: bool = typer.Option(
        False,
        "--show-ids",
        help="Include stable entity IDs in human output, with search-row fallbacks",
    ),
    page: int = typer.Option(1, "--page", min=1, help="Result page to inspect"),
    page_size: int = typer.Option(
        10,
        "--page-size",
        min=1,
        help="Results per page",
    ),
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
    """Show the retrieval stages that produced one query result page."""
    from basic_memory.cli.commands.command_utils import run_with_cleanup
    from fastmcp.exceptions import ToolError

    retrieval_mode = {
        InspectQueryMode.TEXT: SearchRetrievalMode.FTS,
        InspectQueryMode.VECTOR: SearchRetrievalMode.VECTOR,
        InspectQueryMode.HYBRID: SearchRetrievalMode.HYBRID,
    }[mode]
    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)
        with force_routing(local=local, cloud=cloud):
            response = run_with_cleanup(
                run_inspect_query(
                    SearchQuery(text=query_text, retrieval_mode=retrieval_mode),
                    limit=page_size,
                    offset=(page - 1) * page_size,
                    project=project,
                    project_id=project_id,
                )
            )

        output_mode = _resolve_output_mode(json_output, plain)
        if output_mode == "json":
            print(response.model_dump_json(indent=2))
        elif output_mode == "plain":
            _plain_query(response, show_misses=show_misses, show_ids=show_ids)
        else:
            _display_query(response, show_misses=show_misses, show_ids=show_ids)
    except typer.Exit:
        raise
    except (ToolError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    except Exception as exc:  # pragma: no cover
        logger.error(f"Error inspecting retrieval query: {exc}")
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


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
