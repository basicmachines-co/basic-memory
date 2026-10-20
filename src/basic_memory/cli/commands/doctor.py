"""`bm doctor`: report problems in the user's projects and how to fix them (#1798).

Doctor does two things. Its checks and its self-test write nothing to the user's
projects, config or database. (Opening the local database first runs the same
config-to-database project reconciliation every local `bm` command runs, #1334.)

1. It asks the API for a report on each local project. The report lists findings and,
   for each one it can repair, a typed action that this module turns into a command.
2. It runs a write -> file -> index -> search self-test in a child process with its own
   temporary home, config and database. The earlier self-test registered a throwaway
   project in the user's real install and had to work around every policy a real
   project is subject to (#665, #1315, #1323, #1666). A sandbox avoids all of them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Literal, assert_never

from loguru import logger
from pydantic import BaseModel, Field
from rich.console import Console
import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.cli.markup import literal
from basic_memory.config import ConfigManager
from basic_memory.config_models import ProjectMode, default_fastembed_cache_dir, resolve_data_dir
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import KnowledgeClient, ProjectClient, SearchClient
from basic_memory.schemas import ProjectIndexRunResponse
from basic_memory.schemas.base import Entity
from basic_memory.schemas.doctor import (
    DoctorAction,
    DoctorFinding,
    DoctorSeverity,
    IndexProjectAction,
    ReindexSearchAction,
)
from basic_memory.schemas.project_info import ProjectInfoRequest
from basic_memory.schemas.search import SearchQuery
from basic_memory.utils import shell_command

console = Console()

SELF_TEST_TIMEOUT_SECONDS = 300
# Set only by run_sandboxed_self_test, naming the sandbox the child must run inside.
SANDBOX_MARKER = "BASIC_MEMORY_DOCTOR_SANDBOX"
EVIDENCE_LINES_SHOWN = 5


# --- Report model ---


class DoctorFindingOutput(DoctorFinding):
    """A finding plus the command that carries out its action, ready to paste."""

    command: str | None


class DoctorProjectOutput(BaseModel):
    name: str
    findings: tuple[DoctorFindingOutput, ...]
    has_errors: bool


class SelfTestPassed(BaseModel):
    outcome: Literal["passed"] = "passed"


class SelfTestFailed(BaseModel):
    outcome: Literal["failed"] = "failed"
    output: str = Field(description="The end of the self-test's output")


class SelfTestSkipped(BaseModel):
    outcome: Literal["skipped"] = "skipped"


type SelfTestResult = Annotated[
    SelfTestPassed | SelfTestFailed | SelfTestSkipped,
    Field(discriminator="outcome"),
]


class DoctorOutput(BaseModel):
    """Everything `bm doctor --json` prints."""

    projects: tuple[DoctorProjectOutput, ...]
    skipped_cloud_projects: tuple[str, ...]
    self_test: SelfTestResult
    has_errors: bool


def action_command(action: DoctorAction, project_name: str) -> str:
    """Render the local command that carries out an action for one project."""
    match action:
        case IndexProjectAction():
            return shell_command("bm", "project", "index", project_name)
        case ReindexSearchAction(full=full):
            full_flag = ("--full",) if full else ()
            return shell_command("bm", "reindex", "--search", *full_flag, "--project", project_name)
        case _ as unreachable:  # pragma: no cover - exhaustiveness proof
            assert_never(unreachable)


# --- Project inspection ---


async def inspect_projects(
    project: str | None,
) -> tuple[tuple[DoctorProjectOutput, ...], tuple[str, ...]]:
    """Report every local project, or only the named one, without changing any.

    Returns the reports and the cloud projects that were skipped.
    """
    config = ConfigManager().config

    def is_cloud(name: str) -> bool:
        entry = config.project_entry(name)
        return entry is not None and entry.mode is ProjectMode.CLOUD

    # Trigger: a project routes through the cloud.
    # Why: its files and index live on the server, so the local commands doctor
    #   suggests cannot repair it. A cloud-only project also has no local database
    #   row (project sync removes it), so it must come from config, before any
    #   local API lookup that would not find it.
    # Outcome: cloud projects are listed as skipped and never reported.
    if project is not None and is_cloud(project):
        return (), (project,)
    skipped_cloud = (
        ()
        if project is not None
        else tuple(sorted(name for name in config.projects if is_cloud(name)))
    )

    async with get_client() as client:
        project_client = ProjectClient(client)
        if project is not None:
            resolved = await project_client.resolve_project(project)
            selected = [(resolved.name, resolved.external_id)]
        else:
            selected = [
                (item.name, item.external_id)
                for item in (await project_client.list_projects()).projects
            ]

        reports: list[DoctorProjectOutput] = []
        for name, external_id in selected:
            if is_cloud(name):
                # Trigger: a named project was given by external ID, which config
                #   cannot match before resolution, and it is a cloud project.
                # Why: reporting nothing would read as a clean bill of health.
                # Outcome: the resolved name is reported as skipped. In an
                #   all-project run it is already listed from config.
                if project is not None:
                    skipped_cloud = (name,)
                continue
            report = await project_client.get_doctor_report(external_id)
            reports.append(
                DoctorProjectOutput(
                    name=name,
                    findings=tuple(
                        DoctorFindingOutput(
                            **finding.model_dump(),
                            command=(
                                action_command(finding.action, name)
                                if finding.action is not None
                                else None
                            ),
                        )
                        for finding in report.findings
                    ),
                    has_errors=report.has_errors,
                )
            )
    return tuple(reports), skipped_cloud


# --- Sandboxed self-test ---


def sandbox_environment(sandbox: Path, fastembed_cache: str) -> dict[str, str]:
    """Build an environment that cannot reach the user's config, database or projects.

    Every BASIC_MEMORY_* variable is dropped, so a project root, cloud mode, database
    URL or default project set in the user's shell cannot leak into the sandbox. The
    FastEmbed cache is the one thing shared: it holds a large downloaded model, and
    reading it changes nothing.
    """
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("BASIC_MEMORY_")
    }
    sandbox_tmp = str(sandbox / "tmp")
    environment.update(
        {
            # Temp files the self-test creates (its project directory) stay inside
            # the sandbox and are removed with it. TEMP and TMP are the Windows names.
            "TMPDIR": sandbox_tmp,
            "TEMP": sandbox_tmp,
            "TMP": sandbox_tmp,
            "HOME": str(sandbox / "home"),
            "BASIC_MEMORY_HOME": str(sandbox / "home" / "basic-memory"),
            "BASIC_MEMORY_CONFIG_DIR": str(sandbox / "config"),
            "BASIC_MEMORY_NO_PROMOS": "1",
            SANDBOX_MARKER: str(sandbox),
            "FASTEMBED_CACHE_PATH": fastembed_cache,
        }
    )
    return environment


def run_sandboxed_self_test() -> SelfTestPassed | SelfTestFailed:
    """Run `bm doctor --self-test` in a child process inside a throwaway sandbox."""
    config = ConfigManager().config
    fastembed_cache = config.semantic_embedding_cache_dir or default_fastembed_cache_dir()
    with tempfile.TemporaryDirectory(prefix="bm-doctor-") as sandbox_dir:
        sandbox = Path(sandbox_dir)
        for directory in ("home/basic-memory", "config", "tmp"):
            (sandbox / directory).mkdir(parents=True)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "basic_memory.cli.main", "doctor", "--self-test"],
                env=sandbox_environment(sandbox, fastembed_cache),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=SELF_TEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return SelfTestFailed(output=f"timed out after {SELF_TEST_TIMEOUT_SECONDS}s")
    if completed.returncode == 0:
        return SelfTestPassed()
    return SelfTestFailed(output="\n".join(completed.stdout.strip().splitlines()[-20:]))


async def _read_materialized_api_note(api_file: Path, file_path: str) -> str:
    """Wait for the accepted local write, then read its canonical file."""
    # Deferred: importing the materialization runtime at CLI module load would slow every
    # command, while doctor is the only command that needs to observe its write immediately.
    from basic_memory.index.note_content_materialization import drain_pending_materializations

    # Trigger: production accepts note content before its markdown file is materialized.
    # Why: doctor verifies the complete DB -> file contract, not only write acceptance.
    # Outcome: wait for the same deferred work drained at normal CLI shutdown before checking.
    await drain_pending_materializations()

    if not api_file.exists():
        raise ValueError(f"API note file missing: {file_path}")

    return api_file.read_text(encoding="utf-8")


async def run_self_test() -> None:
    """Exercise API -> file and file -> index -> search in the current (sandboxed) install.

    Only `bm doctor --self-test` calls this, and only inside the sandbox built by
    `run_sandboxed_self_test`, so the project it creates is thrown away with the
    sandbox and needs no cleanup.
    """
    # Deferred: the markdown parsing stack is only needed while the checks run,
    # and importing it at module level slows every CLI invocation (#886).
    from basic_memory.markdown.entity_parser import EntityParser
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown

    # Trigger: `bm doctor --self-test` was run by hand, outside the sandbox.
    # Why: the self-test creates a project and never removes it, which is only
    #   safe in a throwaway config. Run against the real one, it would leave a
    #   project behind in the user's real install.
    # Outcome: refuse unless the parent's marker names a sandbox that holds the
    #   config directory this process would write to.
    sandbox = os.environ.get(SANDBOX_MARKER)
    if sandbox is None or not resolve_data_dir().resolve().is_relative_to(Path(sandbox).resolve()):
        raise ValueError(
            "--self-test only runs inside the sandbox bm doctor creates; run bm doctor"
        )

    api_note_title = "Doctor API Note"
    manual_note_title = "Doctor Manual Note"
    project_path = Path(tempfile.mkdtemp(prefix="doctor-project-"))

    async with get_client() as client:
        project_client = ProjectClient(client)
        status = await project_client.create_project(
            ProjectInfoRequest(
                name="doctor", path=str(project_path), set_default=False
            ).model_dump()
        )
        if not status.new_project:
            raise ValueError("Failed to create the self-test project")
        project_id = status.new_project.external_id
        project_path = Path(status.new_project.path)
        print("OK created self-test project")

        # --- DB -> File: create an entity via API ---
        knowledge_client = KnowledgeClient(client, project_id)
        api_note = Entity(
            title=api_note_title,
            directory="doctor",
            note_type="note",
            content_type="text/markdown",
            content=f"# {api_note_title}\n\n- [note] API to file check",
            entity_metadata={"tags": ["doctor"]},
        )
        api_result = await knowledge_client.create_entity(api_note.model_dump())
        api_text = await _read_materialized_api_note(
            project_path / api_result.file_path, api_result.file_path
        )
        if api_note_title not in api_text:
            raise ValueError("API note content missing from file")
        print("OK API write created file")

        # --- File -> DB: write markdown file directly, then index ---
        processor = MarkdownProcessor(EntityParser(project_path))
        manual_markdown = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "title": manual_note_title,
                    "type": "note",
                    "permalink": "doctor/manual-note",
                    "tags": ["doctor"],
                }
            ),
            content=f"# {manual_note_title}\n\n- [note] File to DB check",
        )
        await processor.write_file(project_path / "doctor" / "manual-note.md", manual_markdown)
        print("OK manual file written")

        index_data = await project_client.index(
            project_id, force_full=False, run_in_background=False
        )
        if ProjectIndexRunResponse.model_validate(index_data).enqueued_files == 0:
            raise ValueError("Project index did not enqueue any files")
        print("OK project index processed manual file")

        search_results = await SearchClient(client, project_id).search(
            SearchQuery(title=manual_note_title).model_dump(), page=1, page_size=5
        )
        if not any(result.title == manual_note_title for result in search_results.results):
            raise ValueError("Manual note not found in search index")
        print("OK search confirmed manual file")

        status_report = await project_client.get_status(project_id)
        if "doctor/manual-note.md" not in {file.path for file in status_report.observed_files}:
            raise ValueError("Project index status did not observe manual note")
        print("OK status observed indexed file")


# --- Output ---

_SEVERITY_STYLE = {
    DoctorSeverity.ERROR: "red",
    DoctorSeverity.WARNING: "yellow",
    DoctorSeverity.INFO: "blue",
}
_SEVERITY_ORDER = (DoctorSeverity.ERROR, DoctorSeverity.WARNING, DoctorSeverity.INFO)


def render_output(output: DoctorOutput) -> None:
    """Print the report for a person: problems first, each with its fix."""
    for project in output.projects:
        if not project.findings:
            console.print(f"[green]OK[/green] {literal(project.name)}: no problems found")
            continue
        console.print(f"[bold]{literal(project.name)}[/bold]")
        findings = sorted(
            project.findings, key=lambda finding: _SEVERITY_ORDER.index(finding.severity)
        )
        for finding in findings:
            style = _SEVERITY_STYLE[finding.severity]
            console.print(
                f"  [{style}]{finding.severity.value.upper()}[/{style}] {literal(finding.summary)}"
            )
            for line in finding.evidence[:EVIDENCE_LINES_SHOWN]:
                console.print(f"      {literal(line)}")
            if len(finding.evidence) > EVIDENCE_LINES_SHOWN:
                console.print(f"      ...and {len(finding.evidence) - EVIDENCE_LINES_SHOWN} more")
            if finding.command is not None:
                console.print(f"      fix: [cyan]{literal(finding.command)}[/cyan]")
    if output.skipped_cloud_projects:
        console.print(
            "[dim]Skipped cloud projects (indexed on the server): "
            f"{literal(', '.join(output.skipped_cloud_projects))}[/dim]"
        )
    match output.self_test:
        case SelfTestPassed():
            console.print("[green]OK[/green] Self-test passed (sandboxed write, index and search)")
        case SelfTestFailed(output=failure_output):
            console.print("[red]FAILED[/red] Self-test (sandboxed write, index and search)")
            console.print(literal(failure_output))
        case SelfTestSkipped():
            console.print("[dim]Self-test skipped[/dim]")
        case _ as unreachable:  # pragma: no cover - exhaustiveness proof
            assert_never(unreachable)
    if output.has_errors:
        console.print("[red]Doctor found problems that need a fix.[/red]")
    else:
        console.print("[green]Doctor checks passed.[/green]")


# --- Command ---


@app.command()
def doctor(
    project: str | None = typer.Option(
        None, "--project", "-p", help="Check one project (name or external ID) instead of all"
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the report as JSON"),
    skip_self_test: bool = typer.Option(
        False, "--skip-self-test", help="Only report on projects; skip the sandboxed self-test"
    ),
    self_test: bool = typer.Option(False, "--self-test", hidden=True),
    local: bool = typer.Option(
        False, "--local", help="Check local projects (the default; kept for compatibility)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Not supported yet (#1798)"),
) -> None:
    """Report problems in your projects and the commands that fix them."""
    # Deferred: ToolError lives in FastMCP's runtime, which must not load at CLI startup (#886).
    from fastmcp.exceptions import ToolError

    try:
        validate_routing_flags(local, cloud)
        if cloud:
            raise ValueError("bm doctor checks local projects; cloud support is tracked in #1798")
        with force_routing(local=True, cloud=False):
            if self_test:
                run_with_cleanup(run_self_test())
                return
            projects, skipped_cloud = run_with_cleanup(inspect_projects(project))
        self_test_result: SelfTestResult = (
            SelfTestSkipped() if skip_self_test else run_sandboxed_self_test()
        )
        output = DoctorOutput(
            projects=projects,
            skipped_cloud_projects=skipped_cloud,
            self_test=self_test_result,
            has_errors=(
                any(report.has_errors for report in projects)
                or isinstance(self_test_result, SelfTestFailed)
            ),
        )
    except (ToolError, ValueError) as e:
        # str() of a message-less exception (e.g. httpx.ReadTimeout) is empty;
        # fall back to repr so the failure line always names the error (#1027).
        error_detail = str(e) or repr(e)
        console.print(f"[red]Doctor failed: {literal(error_detail)}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        error_detail = str(e) or repr(e)
        logger.error(f"Doctor failed: {error_detail}")
        typer.echo(f"Doctor failed: {error_detail}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover

    if json_output:
        typer.echo(output.model_dump_json(indent=2))
    else:
        render_output(output)
    if output.has_errors:
        raise typer.Exit(code=1)
