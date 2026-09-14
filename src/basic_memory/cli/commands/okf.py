"""Export and check static OKF v0.2 directory bundles."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from basic_memory.cli.app import app

if TYPE_CHECKING:
    from basic_memory.okf.validation import CheckReport

okf_app = typer.Typer(help="Export and check OKF v0.2-compatible directory bundles")
app.add_typer(okf_app, name="okf")


def print_report(report: CheckReport, json_output: bool) -> None:
    if json_output:
        typer.echo(report.model_dump_json())
    else:
        typer.echo(
            f"{'Valid' if report.success else 'Invalid'} OKF bundle: {report.concepts} concepts"
        )
        for diagnostic in report.diagnostics:
            typer.echo(f"{diagnostic.path}: {diagnostic.rule}: {diagnostic.message}")
    if not report.success:
        raise typer.Exit(1)


@okf_app.command("check")
def check(
    bundle_path: Path = typer.Argument(..., help="Directory bundle to validate"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable diagnostics"),
) -> None:
    """Validate OKF v0.2 structural rules; exit nonzero on violations."""
    from basic_memory.okf.validation import check_bundle

    print_report(check_bundle(bundle_path), json_output)


@okf_app.command("export")
def export(
    destination: Path = typer.Argument(..., help="Destination outside the source project"),
    project: str = typer.Option(..., "--project", "-p", help="Configured local project to export"),
    replace: bool = typer.Option(False, "--replace", help="Replace an existing destination bundle"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable diagnostics"),
) -> None:
    """Stage, validate, and publish a static OKF v0.2-compatible bundle.

    Preserves source files and non-Markdown assets. Links become standard Markdown;
    BM semantics use the bm.okf_export extension. History is best-effort recorded history.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup
    from basic_memory.cli.container import get_or_create_container
    from basic_memory.okf.export import export_project
    from basic_memory.okf.validation import CheckReport, Diagnostic

    try:
        report = run_with_cleanup(
            export_project(get_or_create_container().config, project, destination, replace=replace)
        )
    except (ValueError, OSError, UnicodeError) as error:
        report = CheckReport(
            diagnostics=[Diagnostic(path=str(destination), rule="export", message=str(error))]
        )
    print_report(report, json_output)
