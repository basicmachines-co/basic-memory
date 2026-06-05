"""GitHub CI project update commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Optional, cast

import typer
from pydantic import ValidationError
from rich.console import Console

from basic_memory.ci.project_updates import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROMPT_PATH,
    DEFAULT_WORKFLOW_PATH,
    AgentSynthesis,
    ProjectUpdateConfig,
    ProjectUpdateContext,
    ProjectUpdateNote,
    build_project_update_note,
    collect_project_update_context,
    detect_github_repo,
    load_project_update_config,
    render_agent_synthesis_schema,
    render_capture_prompt,
    render_workflow,
    schema_seed_specs,
    write_project_update_config,
)
from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.mcp.tools import search_notes as mcp_search_notes
from basic_memory.mcp.tools import write_note as mcp_write_note


console = Console()
ci_app = typer.Typer(help="Capture GitHub delivery moments into Basic Memory")
app.add_typer(ci_app, name="ci")


@ci_app.command()
def setup(
    project: Annotated[str, typer.Option(help="Basic Memory project for project updates")],
    repo_root: Annotated[
        Path,
        typer.Option("--repo-root", help="GitHub repository root to configure"),
    ] = Path("."),
    project_id: Annotated[
        Optional[str],
        typer.Option("--project-id", help="Basic Memory project external_id"),
    ] = None,
    workspace: Annotated[
        Optional[str],
        typer.Option("--workspace", help="Cloud workspace slug for generated config"),
    ] = None,
    deploy_workflow: Annotated[
        Optional[list[str]],
        typer.Option("--deploy-workflow", help="Production deploy workflow name"),
    ] = None,
    environment: Annotated[
        Optional[list[str]],
        typer.Option("--environment", help="Production environment name"),
    ] = None,
    force: bool = typer.Option(False, "--force", help="Overwrite generated Auto BM files"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts"),
    local: bool = typer.Option(False, "--local", help="Force local API routing for schema seeding"),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing for schema seeding"),
    refresh_schemas: bool = typer.Option(
        False,
        "--refresh-schemas",
        "--update-schemas",
        "--refresh",
        "--update",
        help="Update existing Auto BM schema notes instead of only seeding missing ones",
    ),
) -> None:
    """Install the GitHub Actions workflow and seed project update schemas."""
    try:
        validate_routing_flags(local, cloud)
        repo_root = repo_root.resolve()
        owner, repo = detect_github_repo(repo_root)
        config = ProjectUpdateConfig(
            project=project,
            project_id=project_id,
            workspace=workspace,
            deploy_workflows=deploy_workflow or ["Deploy Production"],
            production_environments=environment or ["production"],
        )

        if not yes:
            confirmed = typer.confirm(
                f"Install Auto BM for {owner}/{repo} and write updates to {project}?"
            )
            if not confirmed:
                raise typer.Exit(1)

        _write_generated_files(repo_root, config, force=force)

        with force_routing(local=local, cloud=cloud):
            seeded = run_with_cleanup(
                seed_project_update_schemas(
                    project=project,
                    project_id=project_id,
                    workspace=workspace,
                    refresh=refresh_schemas,
                )
            )

        console.print("[green]Auto BM GitHub workflow installed[/green]")
        console.print(f"Repository: {owner}/{repo}")
        console.print(f"Project: {project}")
        if seeded:
            verb = "Updated" if refresh_schemas else "Seeded"
            console.print(f"{verb} schemas: {', '.join(seeded)}")
        else:
            console.print("Schema notes already exist; nothing seeded")
        console.print("\nAdd these GitHub secrets before enabling the workflow:")
        console.print("- OPENAI_API_KEY")
        console.print("- BASIC_MEMORY_API_KEY")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@ci_app.command()
def collect(
    output: Annotated[
        Path,
        typer.Option("--output", help="Where to write ProjectUpdateContext JSON"),
    ],
    config_path: Annotated[
        Path,
        typer.Option("--config", help="Auto BM repository config path"),
    ] = Path(DEFAULT_CONFIG_PATH),
    event_name: Annotated[
        Optional[str],
        typer.Option("--event-name", help="GitHub event name; defaults to GITHUB_EVENT_NAME"),
    ] = None,
    event_path: Annotated[
        Optional[Path],
        typer.Option("--event-path", help="GitHub event payload; defaults to GITHUB_EVENT_PATH"),
    ] = None,
) -> None:
    """Normalize the current GitHub event into project update context JSON."""
    try:
        effective_event_name = event_name or os.environ.get("GITHUB_EVENT_NAME")
        if not effective_event_name:
            raise ValueError("Missing event name. Pass --event-name or set GITHUB_EVENT_NAME.")

        event_path_value = event_path or (
            Path(os.environ["GITHUB_EVENT_PATH"]) if os.environ.get("GITHUB_EVENT_PATH") else None
        )
        if event_path_value is None:
            raise ValueError("Missing event payload. Pass --event-path or set GITHUB_EVENT_PATH.")

        config = load_project_update_config(config_path)
        context = collect_project_update_context(
            event_name=effective_event_name,
            event_path=event_path_value,
            config=config,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(context.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_github_output("eligible", str(context.eligible).lower())
        _write_github_output("skip_reason", context.skip_reason or "")
        console.print(f"Wrote project update context to {output}")
        if not context.eligible:
            console.print(f"Skipped: {context.skip_reason}")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@ci_app.command("agent-schema")
def agent_schema(
    output: Annotated[
        Path,
        typer.Option("--output", help="Where to write the temporary AgentSynthesis schema"),
    ],
) -> None:
    """Write the temporary Codex structured-output schema."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_agent_synthesis_schema(), encoding="utf-8")
    console.print(f"Wrote agent synthesis schema to {output}")


@ci_app.command()
def publish(
    context_path: Annotated[
        Path,
        typer.Option("--context", help="ProjectUpdateContext JSON from bm ci collect"),
    ],
    synthesis_path: Annotated[
        Path,
        typer.Option("--synthesis", help="AgentSynthesis JSON produced by Codex"),
    ],
    config_path: Annotated[
        Path,
        typer.Option("--config", help="Auto BM repository config path"),
    ] = Path(DEFAULT_CONFIG_PATH),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Publish an agent synthesis as an idempotent Basic Memory project update."""
    try:
        validate_routing_flags(local, cloud)
        config = load_project_update_config(config_path)
        context = ProjectUpdateContext.model_validate(_read_json(context_path))
        if not context.eligible:
            console.print(f"Auto BM skipped: {context.skip_reason}")
            return

        synthesis = AgentSynthesis.model_validate(_read_json(synthesis_path))
        note = build_project_update_note(context=context, synthesis=synthesis, config=config)

        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(
                publish_project_update_note(config=config, context=context, note=note)
            )

        console.print(json.dumps(result, indent=2, sort_keys=True, default=str))
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


async def seed_project_update_schemas(
    *,
    project: str | None,
    project_id: str | None = None,
    workspace: str | None = None,
    refresh: bool = False,
) -> list[str]:
    """Seed Auto BM schema notes without overwriting customized schemas."""
    seeded: list[str] = []
    routed_project = _routed_project(project=project, project_id=project_id, workspace=workspace)
    for spec in schema_seed_specs():
        existing = await mcp_search_notes(
            query=None,
            project=routed_project,
            project_id=project_id,
            metadata_filters={"type": "schema", "entity": spec.entity},
            output_format="json",
            page_size=1,
        )
        existing_results = _search_results(existing)
        if existing_results and not refresh:
            continue

        title, directory = _note_write_target(
            existing,
            default_title=spec.title,
            default_directory="schemas",
        )

        await mcp_write_note(
            title=title,
            content=spec.content,
            directory=directory,
            project=routed_project,
            project_id=project_id,
            note_type="schema",
            metadata=spec.metadata,
            overwrite=bool(existing_results) and refresh,
            output_format="json",
        )
        seeded.append(spec.entity)
    return seeded


async def publish_project_update_note(
    *,
    config: ProjectUpdateConfig,
    context: ProjectUpdateContext,
    note: ProjectUpdateNote,
) -> dict[str, Any]:
    """Search by idempotency key and then upsert the deterministic note path."""
    routed_project = _routed_project(
        project=config.project,
        project_id=config.project_id,
        workspace=config.workspace,
    )
    existing = await mcp_search_notes(
        query=None,
        project=routed_project,
        project_id=config.project_id,
        metadata_filters={"type": "project_update", "idempotency_key": context.idempotency_key},
        output_format="json",
        page_size=1,
    )
    title, directory = _note_write_target(
        existing, default_title=note.title, default_directory=note.directory
    )
    result = await mcp_write_note(
        title=title,
        content=note.content,
        directory=directory,
        project=routed_project,
        project_id=config.project_id,
        tags=note.tags,
        note_type="project_update",
        metadata=note.metadata,
        overwrite=True,
        output_format="json",
    )
    if isinstance(result, dict):
        return result
    return {"result": result}


def _note_write_target(
    search_payload: object,
    *,
    default_title: str,
    default_directory: str,
) -> tuple[str, str]:
    """Use an existing idempotency match's path when one exists."""
    results = _search_results(search_payload)
    if not results:
        return default_title, default_directory

    match = results[0]
    if not isinstance(match, dict):
        return default_title, default_directory

    raw_title = match.get("title")
    title = raw_title if isinstance(raw_title, str) else default_title
    file_path = match.get("file_path")
    if isinstance(file_path, str) and file_path.strip():
        parent = Path(file_path).parent.as_posix()
        directory = "" if parent == "." else parent
        return title, directory

    return title, default_directory


def _write_generated_files(repo_root: Path, config: ProjectUpdateConfig, *, force: bool) -> None:
    files = {
        repo_root / DEFAULT_WORKFLOW_PATH: render_workflow(config),
        repo_root / DEFAULT_PROMPT_PATH: render_capture_prompt(),
    }
    config_path = repo_root / DEFAULT_CONFIG_PATH
    _validate_generated_targets([*files, config_path], force=force)
    for path, content in files.items():
        _write_generated_file(path, content, force=force)
    write_project_update_config(config_path, config)


def _validate_generated_targets(paths: list[Path], *, force: bool) -> None:
    if force:
        return
    for path in paths:
        if path.exists():
            raise ValueError(f"{path} already exists; pass --force to overwrite")


def _write_generated_file(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"{path} already exists; pass --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_github_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def _routed_project(
    *,
    project: str | None,
    project_id: str | None,
    workspace: str | None,
) -> str | None:
    """Return a workspace-qualified project route when the config can enforce one."""
    if project_id or not project or not workspace or "/" in project:
        return project
    return f"{workspace.strip('/')}/{project}"


def _search_results(payload: object) -> list[Any]:
    if isinstance(payload, dict):
        payload_dict = cast(dict[str, Any], payload)
        results = payload_dict.get("results")
        if isinstance(results, list):
            return results
        nested = payload_dict.get("result")
        if isinstance(nested, dict) and isinstance(nested.get("results"), list):
            return nested["results"]
    return []
