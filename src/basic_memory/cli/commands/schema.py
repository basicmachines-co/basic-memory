"""Schema management CLI commands for Basic Memory.

Provides CLI access to schema validation, inference, and drift detection.
Registered as a subcommand group: `bm schema validate`, `bm schema infer`, `bm schema diff`.

Each command calls the corresponding MCP tool with output_format="json" and
renders the result as Rich tables — same code path as `bm tool schema-*` but
with human-friendly formatting.
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
from basic_memory.mcp.tools import schema_diff as mcp_schema_diff
from basic_memory.mcp.tools import schema_infer as mcp_schema_infer
from basic_memory.mcp.tools import schema_validate as mcp_schema_validate

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


# --- Rendering helpers ---


def _render_validate_table(data: dict) -> None:
    """Render a validation report dict as a Rich table."""
    note_type = data.get("note_type")
    title_label = note_type or "all"

    table = Table(title=f"Schema Validation: {title_label}")
    table.add_column("Note", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Warnings", justify="right")
    table.add_column("Errors", justify="right")

    for result in data.get("results", []):
        warnings = result.get("warnings", [])
        errors = result.get("errors", [])
        passed = result.get("passed", True)

        if passed and not warnings:
            status = "[green]pass[/green]"
        elif passed:
            status = "[yellow]warn[/yellow]"
        else:
            status = "[red]fail[/red]"

        table.add_row(
            result.get("note_identifier", ""),
            status,
            str(len(warnings)),
            str(len(errors)),
        )

    console.print(table)
    console.print(
        f"\nSummary: {data.get('valid_count', 0)}/{data.get('total_notes', 0)} valid, "
        f"{data.get('warning_count', 0)} warnings, {data.get('error_count', 0)} errors"
    )


def _render_infer_table(data: dict) -> None:
    """Render an inference report dict as a Rich table."""
    note_type = data.get("note_type", "")
    notes_analyzed = data.get("notes_analyzed", 0)
    suggested_required = data.get("suggested_required", [])
    suggested_optional = data.get("suggested_optional", [])

    console.print(f"\n[bold]Analyzing {notes_analyzed} notes with type: {note_type}...[/bold]\n")

    table = Table(title="Field Frequencies")
    table.add_column("Field", style="cyan")
    table.add_column("Source")
    table.add_column("Count", justify="right")
    table.add_column("Percentage", justify="right")
    table.add_column("Suggested")

    for freq in data.get("field_frequencies", []):
        pct = f"{freq.get('percentage', 0):.0%}"
        name = freq.get("name", "")
        if name in suggested_required:
            suggested = "[green]required[/green]"
        elif name in suggested_optional:
            suggested = "[yellow]optional[/yellow]"
        else:
            suggested = "[dim]excluded[/dim]"

        table.add_row(
            name,
            freq.get("source", ""),
            str(freq.get("count", 0)),
            pct,
            suggested,
        )

    console.print(table)

    suggested_schema = data.get("suggested_schema", {})
    if suggested_schema:
        console.print("\n[bold]Suggested schema:[/bold]")
        console.print(json.dumps(suggested_schema, indent=2))


def _render_diff_output(data: dict) -> None:
    """Render a drift report dict as Rich output."""
    note_type = data.get("note_type", "")
    new_fields = data.get("new_fields", [])
    dropped_fields = data.get("dropped_fields", [])
    cardinality_changes = data.get("cardinality_changes", [])

    has_drift = new_fields or dropped_fields or cardinality_changes

    if not has_drift:
        console.print(f"[green]No drift detected for {note_type} schema.[/green]")
        return

    console.print(f"\n[bold]Schema drift detected for {note_type}:[/bold]\n")

    if new_fields:
        console.print("[green]+ New fields (common in notes, not in schema):[/green]")
        for f in new_fields:
            console.print(
                f"  + {f['name']}: {f.get('percentage', 0):.0%} of notes ({f.get('source', '')})"
            )

    if dropped_fields:
        console.print("[red]- Dropped fields (in schema, rare in notes):[/red]")
        for f in dropped_fields:
            console.print(
                f"  - {f['name']}: {f.get('percentage', 0):.0%} of notes ({f.get('source', '')})"
            )

    if cardinality_changes:
        console.print("[yellow]~ Cardinality changes:[/yellow]")
        for change in cardinality_changes:
            console.print(f"  ~ {change}")


# --- Commands ---


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
                    project=project_name,
                    output_format="json",
                )
            )

        # Handle error responses
        if isinstance(result, dict) and "error" in result:
            console.print(f"[yellow]{result['error']}[/yellow]")
            return

        # output_format="json" guarantees a dict return
        assert isinstance(result, dict)
        _render_validate_table(result)

        if strict and result.get("error_count", 0) > 0:
            raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema validate: {e}")
            typer.echo(f"Error during schema validate: {e}", err=True)
            raise typer.Exit(1)
        raise


@schema_app.command()
def infer(
    note_type: Annotated[
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
            result = run_with_cleanup(
                mcp_schema_infer(
                    note_type=note_type,
                    threshold=threshold,
                    project=project_name,
                    output_format="json",
                )
            )

        # Handle error responses
        if isinstance(result, dict) and "error" in result:
            console.print(f"[yellow]{result['error']}[/yellow]")
            return

        # output_format="json" guarantees a dict return
        assert isinstance(result, dict)

        # Handle zero notes
        if result.get("notes_analyzed", 0) == 0:
            console.print(f"[yellow]No notes found with type: {note_type}[/yellow]")
            return

        _render_infer_table(result)

        if save:
            console.print(
                f"\n[yellow]--save not yet implemented. "
                f"Copy the schema above into schema/{note_type}.md[/yellow]"
            )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema infer: {e}")
            typer.echo(f"Error during schema infer: {e}", err=True)
            raise typer.Exit(1)
        raise


@schema_app.command()
def diff(
    note_type: Annotated[
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
            result = run_with_cleanup(
                mcp_schema_diff(
                    note_type=note_type,
                    project=project_name,
                    output_format="json",
                )
            )

        # Handle error responses
        if isinstance(result, dict) and "error" in result:
            console.print(f"[yellow]{result['error']}[/yellow]")
            return

        # output_format="json" guarantees a dict return
        assert isinstance(result, dict)
        _render_diff_output(result)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema diff: {e}")
            typer.echo(f"Error during schema diff: {e}", err=True)
            raise typer.Exit(1)
        raise
