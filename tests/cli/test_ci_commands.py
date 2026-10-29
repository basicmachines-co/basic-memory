import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from basic_memory.cli.commands.ci import _routed_project, seed_project_update_schemas
from basic_memory.mcp.tools import bm_write
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


runner = CliRunner()


def _init_github_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/basicmachines-co/demo.git"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _write_pr_event(path: Path) -> Path:
    payload = {
        "action": "closed",
        "repository": {
            "full_name": "basicmachines-co/demo",
            "html_url": "https://github.com/basicmachines-co/demo",
        },
        "pull_request": {
            "number": 7,
            "title": "Add project update capture",
            "body": "Closes #4",
            "html_url": "https://github.com/basicmachines-co/demo/pull/7",
            "merged": True,
            "merged_at": "2026-06-04T18:42:00Z",
            "merge_commit_sha": "abc123",
            "labels": [{"name": "ci"}],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _synthesis_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "summary": "Auto BM records project updates.",
        "story": (
            "GitHub delivery moments were not leaving durable project memory. "
            "Auto BM collects source facts, asks the agent for the delivery story, "
            "and publishes an idempotent note."
        ),
        "problem_addressed": "GitHub delivery context was lost after merge.",
        "solution": "Publish an idempotent Basic Memory project update from CI.",
        "system_impact": "Future agents can recover the project delivery narrative.",
        "why_it_matters": "Future agents can recover project context.",
        "components_changed": ["basic_memory.ci.project_updates"],
        "complexity_introduced": [],
        "refactors_or_removals": [],
        "user_facing_changes": [],
        "internal_changes": [],
        "verification": [],
        "follow_ups": [],
        "decision_candidates": [],
        "task_candidates": [],
    }
    payload.update(overrides)
    return payload


@patch("basic_memory.cli.commands.ci.seed_project_update_schemas", new_callable=AsyncMock)
def test_setup_writes_workflow_config_and_prompt(
    mock_seed: AsyncMock,
    tmp_path: Path,
) -> None:
    _init_github_repo(tmp_path)

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "setup",
            "--project",
            "team-memory",
            "--repo-root",
            str(tmp_path),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github/workflows/basic-memory.yml").exists()
    assert (tmp_path / ".github/basic-memory/config.yml").exists()
    assert (tmp_path / ".github/basic-memory/memory-ci-capture.md").exists()
    assert (tmp_path / ".github/basic-memory/SOUL.md").exists()
    assert "Keep personality in service of memory" in (
        tmp_path / ".github/basic-memory/SOUL.md"
    ).read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in result.output
    assert "BASIC_MEMORY_API_KEY" in result.output
    mock_seed.assert_awaited_once_with(
        project="team-memory",
        project_id=None,
        workspace=None,
        refresh=False,
    )


@patch("basic_memory.cli.commands.ci.seed_project_update_schemas", new_callable=AsyncMock)
def test_setup_refreshes_or_updates_existing_schema_notes_when_requested(
    mock_seed: AsyncMock,
    tmp_path: Path,
) -> None:
    for flag in ("--refresh", "--update-schemas"):
        repo_path = tmp_path / flag.removeprefix("--")
        repo_path.mkdir()
        _init_github_repo(repo_path)

        result = runner.invoke(
            cli_app,
            [
                "ci",
                "setup",
                "--project",
                "team-memory",
                "--repo-root",
                str(repo_path),
                flag,
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output

    assert mock_seed.await_count == 2
    for seed_call in mock_seed.await_args_list:
        assert seed_call.kwargs == {
            "project": "team-memory",
            "project_id": None,
            "workspace": None,
            "refresh": True,
        }


@patch("basic_memory.cli.commands.ci.seed_project_update_schemas", new_callable=AsyncMock)
def test_setup_refreshes_schema_notes_when_generated_files_already_exist(
    mock_seed: AsyncMock,
    tmp_path: Path,
) -> None:
    _init_github_repo(tmp_path)
    workflow_path = tmp_path / ".github/workflows/basic-memory.yml"
    config_path = tmp_path / ".github/basic-memory/config.yml"
    prompt_path = tmp_path / ".github/basic-memory/memory-ci-capture.md"
    soul_path = tmp_path / ".github/basic-memory/SOUL.md"
    workflow_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    workflow_path.write_text("custom workflow\n", encoding="utf-8")
    config_path.write_text("project: existing\n", encoding="utf-8")
    prompt_path.write_text("custom prompt\n", encoding="utf-8")
    soul_path.write_text("custom soul\n", encoding="utf-8")

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "setup",
            "--project",
            "team-memory",
            "--repo-root",
            str(tmp_path),
            "--refresh-schemas",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "generated files unchanged" in result.output
    assert workflow_path.read_text(encoding="utf-8") == "custom workflow\n"
    assert config_path.read_text(encoding="utf-8") == "project: existing\n"
    assert prompt_path.read_text(encoding="utf-8") == "custom prompt\n"
    assert soul_path.read_text(encoding="utf-8") == "custom soul\n"
    mock_seed.assert_awaited_once_with(
        project="team-memory",
        project_id=None,
        workspace=None,
        refresh=True,
    )


@patch("basic_memory.cli.commands.ci.seed_project_update_schemas", new_callable=AsyncMock)
def test_setup_does_not_partially_write_generated_files_when_target_exists(
    mock_seed: AsyncMock,
    tmp_path: Path,
) -> None:
    _init_github_repo(tmp_path)
    config_path = tmp_path / ".github/basic-memory/config.yml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("project: existing\n", encoding="utf-8")

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "setup",
            "--project",
            "team-memory",
            "--repo-root",
            str(tmp_path),
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "pass --force to overwrite" in " ".join(result.output.split())
    assert not (tmp_path / ".github/workflows/basic-memory.yml").exists()
    assert not (tmp_path / ".github/basic-memory/memory-ci-capture.md").exists()
    mock_seed.assert_not_awaited()


async def test_seed_project_update_schemas_skips_existing_notes_by_default(
    client, test_project
) -> None:
    first = await seed_project_update_schemas(project=test_project.name)
    second = await seed_project_update_schemas(project=test_project.name)

    assert first == ["ProjectUpdate", "GitHubPullRequestUpdate", "GitHubProductionDeployUpdate"]
    assert second == []


async def test_seed_project_update_schemas_refreshes_existing_notes_in_place(
    client, test_project
) -> None:
    """A refresh rewrites a customized schema where it lives, not at the default path."""
    await bm_write(
        content="Customized.",
        identifier="custom/schemas/update.md",
        note_type="schema",
        metadata={"entity": "ProjectUpdate"},
        project=test_project.name,
        context=None,
    )

    seeded = await seed_project_update_schemas(project=test_project.name, refresh=True)

    assert seeded == ["ProjectUpdate", "GitHubPullRequestUpdate", "GitHubProductionDeployUpdate"]
    project_root = Path(test_project.path)
    assert "Customized." not in (project_root / "custom/schemas/update.md").read_text()
    assert not (project_root / "schemas/ProjectUpdate.md").exists()


def test_setup_rejects_non_github_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/basicmachines-co/demo.git"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "setup",
            "--project",
            "team-memory",
            "--repo-root",
            str(tmp_path),
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "GitHub remote" in result.output


def test_collect_command_writes_context_and_github_outputs(tmp_path: Path) -> None:
    event_path = _write_pr_event(tmp_path / "event.json")
    config_path = tmp_path / "config.yml"
    config_path.write_text("project: team-memory\nworkspace: product\n", encoding="utf-8")
    output_path = tmp_path / "context.json"
    github_output = tmp_path / "github-output.txt"

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "collect",
            "--event-name",
            "pull_request",
            "--event-path",
            str(event_path),
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ],
        env={"GITHUB_OUTPUT": str(github_output)},
    )

    assert result.exit_code == 0, result.output
    context = json.loads(output_path.read_text(encoding="utf-8"))
    assert context["eligible"] is True
    assert context["source_event"] == "pull_request_merged"
    assert "eligible=true" in github_output.read_text(encoding="utf-8")


def test_agent_schema_command_writes_schema(tmp_path: Path) -> None:
    output_path = tmp_path / "agent-synthesis.schema.json"

    result = runner.invoke(cli_app, ["ci", "agent-schema", "--output", str(output_path)])

    assert result.exit_code == 0, result.output
    schema = json.loads(output_path.read_text(encoding="utf-8"))
    assert schema["title"] == "AgentSynthesis"


def test_routed_project_prefers_project_id() -> None:
    assert _routed_project(project="team-memory", project_id="uuid", workspace="product") == "uuid"


def test_routed_project_qualifies_a_bare_name_with_the_workspace() -> None:
    routed = _routed_project(project="team-memory", project_id=None, workspace="product")

    assert routed == "product/team-memory"
