import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from basic_memory.cli.commands.ci import seed_project_update_schemas
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
    assert "pass --force to overwrite" in result.output
    assert not (tmp_path / ".github/workflows/basic-memory.yml").exists()
    assert not (tmp_path / ".github/basic-memory/memory-ci-capture.md").exists()
    mock_seed.assert_not_awaited()


@patch("basic_memory.cli.commands.ci.mcp_search_notes", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.ci.mcp_write_note", new_callable=AsyncMock)
async def test_seed_project_update_schemas_skips_existing_notes_by_default(
    mock_write: AsyncMock,
    mock_search: AsyncMock,
) -> None:
    mock_search.return_value = {
        "results": [{"title": "ProjectUpdate", "file_path": "schemas/ProjectUpdate.md"}]
    }

    seeded = await seed_project_update_schemas(project="team-memory")

    assert seeded == []
    mock_write.assert_not_awaited()


@patch("basic_memory.cli.commands.ci.mcp_search_notes", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.ci.mcp_write_note", new_callable=AsyncMock)
async def test_seed_project_update_schemas_refreshes_existing_notes(
    mock_write: AsyncMock,
    mock_search: AsyncMock,
) -> None:
    mock_search.return_value = {
        "results": [{"title": "Custom ProjectUpdate", "file_path": "custom/schemas/update.md"}]
    }
    mock_write.return_value = {"action": "updated"}

    seeded = await seed_project_update_schemas(project="team-memory", refresh=True)

    assert seeded == [
        "ProjectUpdate",
        "GitHubPullRequestUpdate",
        "GitHubProductionDeployUpdate",
    ]
    assert mock_write.await_count == 3
    first_call = mock_write.await_args_list[0].kwargs
    assert first_call["title"] == "Custom ProjectUpdate"
    assert first_call["directory"] == "custom/schemas"
    assert first_call["overwrite"] is True


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


@patch("basic_memory.cli.commands.ci.mcp_search_notes", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.ci.mcp_write_note", new_callable=AsyncMock)
def test_publish_command_upserts_project_update_note(
    mock_write: AsyncMock,
    mock_search: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_search.return_value = {"results": []}
    mock_write.return_value = {
        "title": "PR #7: Add project update capture",
        "permalink": "project-updates/github/basicmachines-co/demo/pr-7-add-project-update-capture",
        "action": "created",
    }
    event_path = _write_pr_event(tmp_path / "event.json")
    context_path = tmp_path / "context.json"
    config_path = tmp_path / "config.yml"
    synthesis_path = tmp_path / "synthesis.json"
    config_path.write_text("project: team-memory\nworkspace: product\n", encoding="utf-8")

    collect_result = runner.invoke(
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
            str(context_path),
        ],
    )
    assert collect_result.exit_code == 0, collect_result.output
    synthesis_path.write_text(
        json.dumps(
            _synthesis_payload(
                repo="evil/repo",
            )
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "publish",
            "--config",
            str(config_path),
            "--context",
            str(context_path),
            "--synthesis",
            str(synthesis_path),
        ],
    )

    assert result.exit_code == 0, result.output
    mock_search.assert_awaited_once()
    mock_write.assert_awaited_once()
    assert mock_search.call_args.kwargs["project"] == "product/team-memory"
    kwargs = mock_write.call_args.kwargs
    assert kwargs["project"] == "product/team-memory"
    assert kwargs["note_type"] == "project_update"
    assert kwargs["overwrite"] is True
    assert kwargs["metadata"]["repo"] == "basicmachines-co/demo"
    assert kwargs["metadata"]["source_event"] == "pull_request_merged"
    assert (
        kwargs["metadata"]["idempotency_key"]
        == "github:basicmachines-co/demo:pull_request_merged:7"
    )


@patch("basic_memory.cli.commands.ci.mcp_search_notes", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.ci.mcp_write_note", new_callable=AsyncMock)
def test_publish_command_preserves_existing_note_path_for_idempotency_match(
    mock_write: AsyncMock,
    mock_search: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_search.return_value = {
        "results": [
            {
                "title": "Existing PR update",
                "file_path": "custom/project-updates/existing-pr-update.md",
            }
        ]
    }
    mock_write.return_value = {"title": "Existing PR update", "action": "updated"}
    event_path = _write_pr_event(tmp_path / "event.json")
    context_path = tmp_path / "context.json"
    config_path = tmp_path / "config.yml"
    synthesis_path = tmp_path / "synthesis.json"
    config_path.write_text("project: team-memory\n", encoding="utf-8")

    collect_result = runner.invoke(
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
            str(context_path),
        ],
    )
    assert collect_result.exit_code == 0, collect_result.output
    synthesis_path.write_text(
        json.dumps(_synthesis_payload()),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "publish",
            "--config",
            str(config_path),
            "--context",
            str(context_path),
            "--synthesis",
            str(synthesis_path),
        ],
    )

    assert result.exit_code == 0, result.output
    kwargs = mock_write.call_args.kwargs
    assert kwargs["title"] == "Existing PR update"
    assert kwargs["directory"] == "custom/project-updates"


@patch("basic_memory.cli.commands.ci.mcp_search_notes", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.ci.mcp_write_note", new_callable=AsyncMock)
def test_publish_command_uses_project_id_without_workspace_qualifying_project(
    mock_write: AsyncMock,
    mock_search: AsyncMock,
    tmp_path: Path,
) -> None:
    mock_search.return_value = {"results": []}
    mock_write.return_value = {"title": "Project update", "action": "created"}
    event_path = _write_pr_event(tmp_path / "event.json")
    context_path = tmp_path / "context.json"
    config_path = tmp_path / "config.yml"
    synthesis_path = tmp_path / "synthesis.json"
    config_path.write_text(
        "project: team-memory\nproject_id: project-uuid\nworkspace: product\n",
        encoding="utf-8",
    )

    collect_result = runner.invoke(
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
            str(context_path),
        ],
    )
    assert collect_result.exit_code == 0, collect_result.output
    synthesis_path.write_text(
        json.dumps(_synthesis_payload()),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_app,
        [
            "ci",
            "publish",
            "--config",
            str(config_path),
            "--context",
            str(context_path),
            "--synthesis",
            str(synthesis_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert mock_search.call_args.kwargs["project"] == "team-memory"
    assert mock_search.call_args.kwargs["project_id"] == "project-uuid"
    assert mock_write.call_args.kwargs["project"] == "team-memory"
    assert mock_write.call_args.kwargs["project_id"] == "project-uuid"
