"""Build and inspect deterministic Wiki navigation for local projects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
import typer
from rich.console import Console

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectMode

if TYPE_CHECKING:
    from basic_memory.index.local_wiki_projection import LocalWikiInspection

console = Console()
wiki_app = typer.Typer(help="Build and inspect deterministic Wiki navigation")
app.add_typer(wiki_app, name="wiki")

type WikiCommandName = Literal["status", "validate", "rebuild"]
type WikiReportState = Literal[
    "current",
    "uninitialized",
    "outdated",
    "partial",
    "conflicted",
]


class WikiProjectReport(BaseModel):
    """Machine-readable result for one local project."""

    project: str
    path: str
    state: WikiReportState
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    writes: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    pending_materialization: list[int] = Field(default_factory=list)


class WikiCommandReport(BaseModel):
    """Stable JSON boundary shared by Wiki CLI commands and aliases."""

    command: WikiCommandName
    dry_run: bool = False
    success: bool
    projects: list[WikiProjectReport]


@wiki_app.command("status")
def wiki_status(
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project to inspect (defaults to the default project)",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Inspect every local project"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """Show whether generated Wiki navigation is current."""
    _run_wiki_command(
        command="status",
        project=project,
        all_projects=all_projects,
        dry_run=False,
        json_output=json_output,
    )


@wiki_app.command("validate")
def wiki_validate(
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project to validate (defaults to the default project)",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Validate every local project"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """Validate generated Wiki documents; exit nonzero when repair is needed."""
    _run_wiki_command(
        command="validate",
        project=project,
        all_projects=all_projects,
        dry_run=False,
        json_output=json_output,
    )


@wiki_app.command("rebuild")
def wiki_rebuild(
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project to rebuild (defaults to the default project)",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Rebuild every local project"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview generated writes"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """Idempotently build every generated index.md and log.md."""
    _run_rebuild_alias(
        project=project,
        all_projects=all_projects,
        dry_run=dry_run,
        json_output=json_output,
    )


@wiki_app.command("init")
def wiki_init(
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project to rebuild (defaults to the default project)",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Rebuild every local project"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview generated writes"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """Alias for `bm wiki rebuild`."""
    _run_rebuild_alias(
        project=project,
        all_projects=all_projects,
        dry_run=dry_run,
        json_output=json_output,
    )


@wiki_app.command("update")
def wiki_update(
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project to rebuild (defaults to the default project)",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Rebuild every local project"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview generated writes"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """Alias for `bm wiki rebuild`."""
    _run_rebuild_alias(
        project=project,
        all_projects=all_projects,
        dry_run=dry_run,
        json_output=json_output,
    )


def _run_rebuild_alias(
    *,
    project: str | None,
    all_projects: bool,
    dry_run: bool,
    json_output: bool,
) -> None:
    _run_wiki_command(
        command="rebuild",
        project=project,
        all_projects=all_projects,
        dry_run=dry_run,
        json_output=json_output,
    )


def _run_wiki_command(
    *,
    command: WikiCommandName,
    project: str | None,
    all_projects: bool,
    dry_run: bool,
    json_output: bool,
) -> None:
    if project is not None and all_projects:
        console.print("[red]Use either --project or --all, not both.[/red]")
        raise typer.Exit(1)

    from basic_memory.index.local_wiki_projection import LocalWikiWriteConflict

    try:
        report = run_with_cleanup(
            _execute_wiki_command(
                ConfigManager().config,
                command=command,
                project_name=project,
                all_projects=all_projects,
                dry_run=dry_run,
            )
        )
    except (LocalWikiWriteConflict, OSError, ValueError) as error:
        console.print(f"[red]Wiki {command} failed: {error}[/red]")
        raise typer.Exit(1) from error

    if json_output:
        typer.echo(report.model_dump_json())
    else:
        _render_report(report)
    if not report.success:
        raise typer.Exit(1)


async def _execute_wiki_command(
    app_config: BasicMemoryConfig,
    *,
    command: WikiCommandName,
    project_name: str | None,
    all_projects: bool,
    dry_run: bool,
) -> WikiCommandReport:
    # Heavy runtime imports remain behind the command boundary so `bm --help`
    # keeps the CLI's lightweight startup contract.
    from basic_memory import db
    from basic_memory.index.local_project import (
        LocalProjectIndexRuntimeFactory,
        run_local_project_index_for_project,
    )
    from basic_memory.index.local_wiki_projection import (
        LocalWikiState,
        apply_local_wiki_projection,
        inspect_local_wiki_projection,
    )
    from basic_memory.repository import ProjectRepository
    from basic_memory.services.initialization import (
        reconcile_projects_with_config,
        recover_project_materializations,
    )

    selected_names = _selected_local_project_names(
        app_config,
        project_name=project_name,
        all_projects=all_projects,
    )
    await reconcile_projects_with_config(app_config)
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    async with db.scoped_session(session_maker) as session:
        active_projects = await ProjectRepository().get_active_projects(session)
    projects_by_name = {project.name: project for project in active_projects}
    missing_names = [name for name in selected_names if name not in projects_by_name]
    if missing_names:
        raise ValueError(f"Local project not found: {', '.join(missing_names)}")

    reports: list[WikiProjectReport] = []
    for name in selected_names:
        project = projects_by_name[name]
        if command == "rebuild" and not dry_run:
            await recover_project_materializations(project, session_maker)
        inspection = await inspect_local_wiki_projection(
            project,
            session_maker=session_maker,
        )
        state = inspection.state
        if (
            command == "rebuild"
            and not dry_run
            and state
            not in {
                LocalWikiState.partial,
                LocalWikiState.conflicted,
            }
        ):
            await apply_local_wiki_projection(
                inspection,
                session_maker=session_maker,
            )
            # The files are canonical locally; indexing makes the generated
            # navigation immediately available through API, MCP, and search.
            await run_local_project_index_for_project(
                project,
                runtime_factory=LocalProjectIndexRuntimeFactory(),
                force_full=False,
            )
            state = LocalWikiState.current
        reports.append(_project_report(inspection, state=state.value))

    success = _command_succeeded(command, reports=reports, dry_run=dry_run)
    return WikiCommandReport(
        command=command,
        dry_run=dry_run,
        success=success,
        projects=reports,
    )


def _selected_local_project_names(
    app_config: BasicMemoryConfig,
    *,
    project_name: str | None,
    all_projects: bool,
) -> list[str]:
    if all_projects:
        local_entries = {
            name: entry
            for name, entry in app_config.projects.items()
            if entry.mode == ProjectMode.LOCAL
        }
        unsafe_names = sorted(
            name
            for name, entry in app_config.projects.items()
            if entry.mode == ProjectMode.LOCAL
            and not app_config.is_locally_syncable(name, entry.path)
        )
        if unsafe_names:
            raise ValueError("Local project paths must be absolute: " + ", ".join(unsafe_names))
        names = sorted(local_entries)
        if not names:
            raise ValueError("No local projects are configured")
        return names

    selected = project_name or app_config.default_project
    if selected is None:
        raise ValueError("No project given and no default project is set; pass --project")
    entry = app_config.projects.get(selected)
    if entry is None:
        raise ValueError(f"Project '{selected}' is not configured")
    if entry.mode == ProjectMode.CLOUD:
        raise ValueError(
            f"Project '{selected}' is a cloud project; Core Wiki commands operate locally"
        )
    if not app_config.is_locally_syncable(selected, entry.path):
        raise ValueError(f"Local project '{selected}' must have an absolute path")
    return [selected]


def _project_report(
    inspection: LocalWikiInspection,
    *,
    state: WikiReportState,
) -> WikiProjectReport:
    plan = inspection.plan
    return WikiProjectReport(
        project=inspection.project_name,
        path=str(inspection.project_root),
        state=state,
        created=plan.result.created,
        updated=plan.result.updated,
        unchanged=plan.result.unchanged,
        writes=[write.path for write in plan.writes],
        conflicts=[f"{conflict.path}: {conflict.reason}" for conflict in plan.result.conflicts],
        warnings=list(plan.result.warnings),
        pending_materialization=list(plan.result.pending_materialization),
    )


def _command_succeeded(
    command: WikiCommandName,
    *,
    reports: list[WikiProjectReport],
    dry_run: bool,
) -> bool:
    if command == "status":
        return True
    if command == "validate":
        return all(report.state == "current" for report in reports)
    if dry_run:
        return all(report.state not in {"partial", "conflicted"} for report in reports)
    return all(report.state == "current" for report in reports)


def _render_report(report: WikiCommandReport) -> None:
    for project in report.projects:
        color = "green" if project.state == "current" else "yellow"
        if project.state == "conflicted":
            color = "red"
        counts = (
            f"create {project.created}, update {project.updated}, unchanged {project.unchanged}"
        )
        prefix = "would rebuild" if report.dry_run else project.state
        console.print(f"[{color}]{project.project}: {prefix}[/{color}] ({counts})")
        for conflict in project.conflicts:
            console.print(f"  [red]- {conflict}[/red]")
        for warning in project.warnings:
            console.print(f"  [yellow]- {warning}[/yellow]")
