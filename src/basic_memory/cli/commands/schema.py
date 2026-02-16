"""Schema management CLI commands for Basic Memory.

Provides CLI access to schema validation, inference, and drift detection.
Registered as a subcommand group: `bm schema validate`, `bm schema infer`, `bm schema diff`.
"""

import json
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.project_context import get_active_project

console = Console()

schema_app = typer.Typer(help="Schema management commands")
app.add_typer(schema_app, name="schema")


def _resolve_project_name(project: Optional[str]) -> Optional[str]:
    """Resolve project name from CLI argument or config default."""
    config_manager = ConfigManager()
    if project is not None:
        project_name, _ = config_manager.get_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)
        return project_name
    return config_manager.default_project


# --- Validate ---


async def _run_validate(
    target: Optional[str] = None,
    project: Optional[str] = None,
    strict: bool = False,
):
    """Run schema validation via the API."""
    from basic_memory.mcp.clients.schema import SchemaClient

    async with get_client(project_name=project) as client:
        active_project = await get_active_project(client, project, None)
        schema_client = SchemaClient(client, active_project.external_id)

        # Determine if target is a note identifier or note type
        # Heuristic: if target contains / or ., treat as identifier
        entity_type = None
        identifier = None
        if target:
            if "/" in target or "." in target:
                identifier = target
            else:
                entity_type = target

        report = await schema_client.validate(
            entity_type=entity_type,
            identifier=identifier,
        )

        # --- Display results ---
        if report.total_notes == 0:
            console.print("[yellow]No notes matched for validation.[/yellow]")
            return

        table = Table(title=f"Schema Validation: {entity_type or identifier or 'all'}")
        table.add_column("Note", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Warnings", justify="right")
        table.add_column("Errors", justify="right")

        for result in report.results:
            if result.passed and not result.warnings:
                status = "[green]pass[/green]"
            elif result.passed:
                status = "[yellow]warn[/yellow]"
            else:
                status = "[red]fail[/red]"

            table.add_row(
                result.note_identifier,
                status,
                str(len(result.warnings)),
                str(len(result.errors)),
            )

        console.print(table)
        console.print(
            f"\nSummary: {report.valid_count}/{report.total_notes} valid, "
            f"{report.warning_count} warnings, {report.error_count} errors"
        )

        # Exit with error code in strict mode if there are failures
        if strict and report.error_count > 0:
            raise typer.Exit(1)


@schema_app.command()
def validate(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Note path or note type to validate"),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    strict: bool = typer.Option(False, "--strict", help="Exit with error on validation failures"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Validate notes against their schemas.

    TARGET can be a note path (e.g., people/ada-lovelace.md) or a note type
    (e.g., person). If omitted, validates all notes that have schemas.

    Use --strict to exit with error code 1 if any validation errors are found.
    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)
        project_name = _resolve_project_name(project)
        with force_routing(local=local, cloud=cloud):
            run_with_cleanup(_run_validate(target, project_name, strict))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema validate: {e}")
            typer.echo(f"Error during schema validate: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- Infer ---


async def _run_infer(
    entity_type: str,
    project: Optional[str] = None,
    threshold: float = 0.25,
    save: bool = False,
):
    """Run schema inference via the API."""
    from basic_memory.mcp.clients.schema import SchemaClient

    async with get_client(project_name=project) as client:
        active_project = await get_active_project(client, project, None)
        schema_client = SchemaClient(client, active_project.external_id)

        report = await schema_client.infer(entity_type, threshold=threshold)

        if report.notes_analyzed == 0:
            console.print(f"[yellow]No notes found with type: {entity_type}[/yellow]")
            return

        # --- Empty schema guard ---
        # Trigger: notes were analyzed but no fields met the threshold
        # Why: dumping hundreds of excluded fields is not useful output
        # Outcome: show count and suggest a more specific type
        if not report.suggested_schema:
            console.print(
                f"\n[yellow]Analyzed {report.notes_analyzed} notes of type '{entity_type}', "
                f"but no fields met the {threshold:.0%} threshold.[/yellow]\n"
            )
            console.print(
                f"This usually means '{entity_type}' is too broad — "
                f"the notes don't share a consistent structure.\n"
            )
            console.print("[bold]Suggestions:[/bold]")
            console.print("  1. Use a more specific type")
            console.print(
                f"  2. Lower the threshold: bm schema infer {entity_type} --threshold 0.1"
            )
            console.print("  3. Create typed notes with write_note using a specific note_type")
            return

        # --- Display frequency analysis ---
        console.print(
            f"\n[bold]Analyzing {report.notes_analyzed} notes with type: {entity_type}...[/bold]\n"
        )

        table = Table(title="Field Frequencies")
        table.add_column("Field", style="cyan")
        table.add_column("Source")
        table.add_column("Count", justify="right")
        table.add_column("Percentage", justify="right")
        table.add_column("Suggested")

        for freq in report.field_frequencies:
            pct = f"{freq.percentage:.0%}"
            if freq.name in report.suggested_required:
                suggested = "[green]required[/green]"
            elif freq.name in report.suggested_optional:
                suggested = "[yellow]optional[/yellow]"
            else:
                suggested = "[dim]excluded[/dim]"

            table.add_row(
                freq.name,
                freq.source,
                str(freq.count),
                pct,
                suggested,
            )

        console.print(table)

        # --- Display suggested schema ---
        console.print("\n[bold]Suggested schema:[/bold]")
        console.print(json.dumps(report.suggested_schema, indent=2))

        if save:
            console.print(
                f"\n[yellow]--save not yet implemented. "
                f"Copy the schema above into schema/{entity_type}.md[/yellow]"
            )


@schema_app.command()
def infer(
    entity_type: Annotated[
        str,
        typer.Argument(help="Note type to analyze (e.g., person, meeting)"),
    ],
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    threshold: float = typer.Option(
        0.25, "--threshold", help="Minimum frequency for optional fields (0-1)"
    ),
    save: bool = typer.Option(False, "--save", help="Save inferred schema to schema/ directory"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Infer schema from existing notes of a type.

    Analyzes all notes with the given type and suggests a Picoschema
    definition based on observation and relation frequency.

    Fields present in 95%+ of notes become required. Fields above the
    threshold (default 25%) become optional. Fields below threshold are excluded.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)
        project_name = _resolve_project_name(project)
        with force_routing(local=local, cloud=cloud):
            run_with_cleanup(_run_infer(entity_type, project_name, threshold, save))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema infer: {e}")
            typer.echo(f"Error during schema infer: {e}", err=True)
            raise typer.Exit(1)
        raise


# --- Diff ---


async def _run_diff(
    entity_type: str,
    project: Optional[str] = None,
):
    """Run schema drift detection via the API."""
    from basic_memory.mcp.clients.schema import SchemaClient

    async with get_client(project_name=project) as client:
        active_project = await get_active_project(client, project, None)
        schema_client = SchemaClient(client, active_project.external_id)

        report = await schema_client.diff(entity_type)

        has_drift = report.new_fields or report.dropped_fields or report.cardinality_changes

        if not has_drift:
            console.print(f"[green]No drift detected for {entity_type} schema.[/green]")
            return

        console.print(f"\n[bold]Schema drift detected for {entity_type}:[/bold]\n")

        if report.new_fields:
            console.print("[green]+ New fields (common in notes, not in schema):[/green]")
            for f in report.new_fields:
                console.print(f"  + {f.name}: {f.percentage:.0%} of notes ({f.source})")

        if report.dropped_fields:
            console.print("[red]- Dropped fields (in schema, rare in notes):[/red]")
            for f in report.dropped_fields:
                console.print(f"  - {f.name}: {f.percentage:.0%} of notes ({f.source})")

        if report.cardinality_changes:
            console.print("[yellow]~ Cardinality changes:[/yellow]")
            for change in report.cardinality_changes:
                console.print(f"  ~ {change}")


@schema_app.command()
def diff(
    entity_type: Annotated[
        str,
        typer.Argument(help="Note type to check for drift"),
    ],
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Show drift between schema and actual usage.

    Compares the existing schema definition against how notes of that type
    are actually structured. Identifies new fields,
    dropped fields, and cardinality changes.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)
        project_name = _resolve_project_name(project)
        with force_routing(local=local, cloud=cloud):
            run_with_cleanup(_run_diff(entity_type, project_name))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema diff: {e}")
            typer.echo(f"Error during schema diff: {e}", err=True)
            raise typer.Exit(1)
        raise
