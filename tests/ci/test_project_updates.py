import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from basic_memory.ci.project_updates import (
    AgentSynthesis,
    ProjectUpdateConfig,
    ProjectUpdateContext,
    build_project_update_note,
    collect_project_update_context,
    detect_github_repo,
    load_project_update_config,
    parse_github_remote,
    render_agent_synthesis_schema,
    render_capture_prompt,
    render_workflow,
    schema_seed_specs,
)
from basic_memory.ci import project_updates


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pr_payload(*, merged: bool = True) -> dict:
    return {
        "action": "closed",
        "repository": {
            "full_name": "basicmachines-co/basic-memory",
            "html_url": "https://github.com/basicmachines-co/basic-memory",
        },
        "pull_request": {
            "number": 123,
            "title": "Remember project updates",
            "body": "Adds Auto BM capture.\n\nCloses #77",
            "html_url": "https://github.com/basicmachines-co/basic-memory/pull/123",
            "merged": merged,
            "merged_at": "2026-06-04T18:42:00Z" if merged else None,
            "merge_commit_sha": "abc123",
            "changed_files": 4,
            "labels": [{"name": "feature"}, {"name": "ci"}],
            "user": {"login": "octocat"},
        },
    }


def test_collect_merged_pull_request_context(tmp_path: Path) -> None:
    event_path = _write_json(tmp_path / "event.json", _pr_payload())

    context = collect_project_update_context(
        event_name="pull_request",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is True
    assert context.source_event == "pull_request_merged"
    assert context.repo == "basicmachines-co/basic-memory"
    assert context.idempotency_key == "github:basicmachines-co/basic-memory:pull_request_merged:123"
    assert context.pr_number == 123
    assert context.sha == "abc123"
    assert context.labels == ["feature", "ci"]
    assert context.linked_issues == ["#77"]
    assert context.source_url == "https://github.com/basicmachines-co/basic-memory/pull/123"


def test_collect_handles_sparse_pull_request_payload(tmp_path: Path) -> None:
    payload = {
        "action": "closed",
        "repository": {},
        "pull_request": {
            "number": 123,
            "merged": True,
            "labels": "not-a-list",
        },
    }
    event_path = _write_json(tmp_path / "event.json", payload)

    context = collect_project_update_context(
        event_name="pull_request",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is True
    assert context.repo is None
    assert context.repo_url is None
    assert context.labels == []
    assert context.linked_issues == []


def test_collect_handles_missing_repository_payload(tmp_path: Path) -> None:
    payload = {
        "action": "closed",
        "pull_request": {
            "number": 123,
            "merged": True,
        },
    }
    event_path = _write_json(tmp_path / "event.json", payload)

    context = collect_project_update_context(
        event_name="pull_request",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is True
    assert context.repo is None
    assert context.repo_url is None


def test_collect_rejects_missing_payload_shapes(tmp_path: Path) -> None:
    pr_context = collect_project_update_context(
        event_name="pull_request",
        event_path=_write_json(tmp_path / "pr.json", {"action": "closed"}),
        config=ProjectUpdateConfig(project="team-memory"),
    )
    workflow_context = collect_project_update_context(
        event_name="workflow_run",
        event_path=_write_json(tmp_path / "workflow.json", {"action": "completed"}),
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert pr_context.eligible is False
    assert pr_context.skip_reason == "pull request payload missing"
    assert workflow_context.eligible is False
    assert workflow_context.skip_reason == "workflow run payload missing"


def test_collect_ignores_non_closed_pull_request_action(tmp_path: Path) -> None:
    payload = _pr_payload()
    payload["action"] = "opened"
    event_path = _write_json(tmp_path / "event.json", payload)

    context = collect_project_update_context(
        event_name="pull_request",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is False
    assert context.skip_reason == "pull request action was not closed"


def test_collect_ignores_closed_unmerged_pull_request(tmp_path: Path) -> None:
    event_path = _write_json(tmp_path / "event.json", _pr_payload(merged=False))

    context = collect_project_update_context(
        event_name="pull_request",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is False
    assert context.skip_reason == "pull request was closed without merging"


def test_collect_successful_configured_production_deploy(tmp_path: Path) -> None:
    payload = {
        "action": "completed",
        "repository": {
            "full_name": "basicmachines-co/basic-memory-cloud",
            "html_url": "https://github.com/basicmachines-co/basic-memory-cloud",
        },
        "workflow_run": {
            "id": 98765,
            "name": "Deploy Production",
            "conclusion": "success",
            "html_url": "https://github.com/basicmachines-co/basic-memory-cloud/actions/runs/98765",
            "head_sha": "def456",
            "updated_at": "2026-06-04T19:10:00Z",
        },
    }
    event_path = _write_json(tmp_path / "event.json", payload)

    context = collect_project_update_context(
        event_name="workflow_run",
        event_path=event_path,
        config=ProjectUpdateConfig(
            project="cloud-memory",
            deploy_workflows=["Deploy Production"],
            production_environments=["production"],
        ),
    )

    assert context.eligible is True
    assert context.source_event == "production_deploy_succeeded"
    assert context.workflow_run_id == "98765"
    assert context.environment == "production"
    assert context.idempotency_key == (
        "github:basicmachines-co/basic-memory-cloud:production_deploy_succeeded:production:98765"
    )


def test_collect_ignores_failed_or_unconfigured_deploy(tmp_path: Path) -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": "basicmachines-co/basic-memory"},
        "workflow_run": {"id": 1, "name": "Tests", "conclusion": "failure"},
    }
    event_path = _write_json(tmp_path / "event.json", payload)

    context = collect_project_update_context(
        event_name="workflow_run",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is False
    assert context.skip_reason == "workflow conclusion was failure"


def test_collect_ignores_successful_unconfigured_deploy(tmp_path: Path) -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": "basicmachines-co/basic-memory"},
        "workflow_run": {"id": 1, "name": "Tests", "conclusion": "success"},
    }
    event_path = _write_json(tmp_path / "event.json", payload)

    context = collect_project_update_context(
        event_name="workflow_run",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is False
    assert context.skip_reason == "workflow 'Tests' is not configured for project updates"


def test_collect_ignores_unsupported_event(tmp_path: Path) -> None:
    event_path = _write_json(tmp_path / "event.json", {})

    context = collect_project_update_context(
        event_name="push",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )

    assert context.eligible is False
    assert context.skip_reason == "unsupported GitHub event: push"


def test_collect_rejects_missing_or_invalid_event_payload(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        collect_project_update_context(
            event_name="pull_request",
            event_path=tmp_path / "missing.json",
            config=ProjectUpdateConfig(project="team-memory"),
        )

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        collect_project_update_context(
            event_name="pull_request",
            event_path=invalid_json,
            config=ProjectUpdateConfig(project="team-memory"),
        )

    list_json = tmp_path / "list.json"
    list_json.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        collect_project_update_context(
            event_name="pull_request",
            event_path=list_json,
            config=ProjectUpdateConfig(project="team-memory"),
        )


def test_build_project_update_note_uses_deterministic_identity_fields(tmp_path: Path) -> None:
    event_path = _write_json(tmp_path / "event.json", _pr_payload())
    context = collect_project_update_context(
        event_name="pull_request",
        event_path=event_path,
        config=ProjectUpdateConfig(project="team-memory"),
    )
    synthesis = AgentSynthesis.model_validate(
        {
            "summary": "Auto BM now records project updates.",
            "why_it_matters": "Future agents can recover the delivery narrative.",
            "repo": "evil/repo",
            "source_event": "production_deploy_succeeded",
            "verification": ["Unit tests cover event normalization."],
        }
    )

    note = build_project_update_note(context=context, synthesis=synthesis)

    assert note.title == "PR #123: Remember project updates"
    assert note.directory == "project-updates/github/basicmachines-co/basic-memory"
    assert note.metadata["repo"] == "basicmachines-co/basic-memory"
    assert note.metadata["source_event"] == "pull_request_merged"
    assert note.metadata["idempotency_key"] == context.idempotency_key
    assert "evil/repo" not in note.content


def test_build_project_update_note_for_production_deploy(tmp_path: Path) -> None:
    payload = {
        "action": "completed",
        "repository": {
            "full_name": "basicmachines-co/basic-memory-cloud",
            "html_url": "https://github.com/basicmachines-co/basic-memory-cloud",
        },
        "workflow_run": {
            "id": 98765,
            "name": "Deploy Production",
            "conclusion": "success",
            "html_url": "https://github.com/basicmachines-co/basic-memory-cloud/actions/runs/98765",
            "head_sha": "def456",
            "updated_at": "2026-06-04T19:10:00Z",
        },
    }
    context = collect_project_update_context(
        event_name="workflow_run",
        event_path=_write_json(tmp_path / "event.json", payload),
        config=ProjectUpdateConfig(
            project="cloud-memory",
            deploy_workflows=["Deploy Production"],
            production_environments=["production"],
        ),
    )
    synthesis = AgentSynthesis(
        summary="Production deploy completed.",
        why_it_matters="The latest project update reached users.",
    )

    note = build_project_update_note(context=context, synthesis=synthesis)

    assert note.title == "Production deploy: 2026-06-04"
    assert note.metadata["workflow_run_id"] == "98765"
    assert note.metadata["environment"] == "production"
    assert "https://github.com/basicmachines-co/basic-memory-cloud/actions/runs/98765" in (
        note.content
    )


def test_build_project_update_note_rejects_invalid_context() -> None:
    synthesis = AgentSynthesis(
        summary="Auto BM records project updates.",
        why_it_matters="Future agents can recover context.",
    )
    with pytest.raises(ValueError, match="ineligible"):
        build_project_update_note(
            context=ProjectUpdateContext(eligible=False, skip_reason="not useful"),
            synthesis=synthesis,
        )

    with pytest.raises(ValueError, match="deterministic identity"):
        build_project_update_note(
            context=ProjectUpdateContext(
                eligible=True,
                source_event="pull_request_merged",
                repo="basicmachines-co/basic-memory",
            ),
            synthesis=synthesis,
        )


def test_agent_synthesis_requires_summary_and_why_it_matters() -> None:
    with pytest.raises(ValidationError):
        AgentSynthesis.model_validate({"summary": "Too thin"})

    with pytest.raises(ValidationError):
        AgentSynthesis.model_validate({"summary": " ", "why_it_matters": "Still too thin"})


def test_project_update_config_requires_non_empty_lists() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ProjectUpdateConfig(deploy_workflows=[" "])


def test_render_workflow_invokes_codex_read_only_without_basic_memory_secret() -> None:
    workflow = render_workflow(
        ProjectUpdateConfig(
            project="team-memory",
            deploy_workflows=["Deploy Production"],
            production_environments=["production"],
        )
    )

    assert "openai/codex-action@v1" in workflow
    assert "sandbox: read-only" in workflow
    assert "output-schema-file: ${{ runner.temp }}/agent-synthesis.schema.json" in workflow
    assert "BASIC_MEMORY_CLOUD_API_KEY: ${{ secrets.BASIC_MEMORY_API_KEY }}" in workflow
    assert "BASIC_MEMORY_CLOUD_HOST: ${{ vars.BASIC_MEMORY_CLOUD_HOST || '' }}" not in workflow
    assert "BASIC_MEMORY_CI_CLOUD_HOST: ${{ vars.BASIC_MEMORY_CLOUD_HOST }}" in workflow
    assert 'if [ -n "$BASIC_MEMORY_CI_CLOUD_HOST" ]' in workflow
    assert "--context .github/basic-memory/project-update-context.json" in workflow
    assert "--cloud \\" in workflow
    codex_step = workflow.split("- name: Synthesize project update with Codex", 1)[1].split(
        "- name: Publish project update", 1
    )[0]
    assert "BASIC_MEMORY_API_KEY" not in codex_step


def test_render_workflow_outputs_valid_github_actions_yaml() -> None:
    workflow = render_workflow(ProjectUpdateConfig(project="team-memory"))

    parsed = yaml.safe_load(workflow)

    assert isinstance(parsed, dict)
    assert parsed["on"]["pull_request"]["types"] == ["closed"]
    assert parsed["on"]["workflow_run"]["types"] == ["completed"]


def test_render_capture_prompt_uses_workspace_context_path() -> None:
    prompt = render_capture_prompt()

    assert ".github/basic-memory/project-update-context.json" in prompt
    assert "${{ runner.temp }}" not in prompt


def test_render_agent_synthesis_schema_is_ci_guardrail_not_domain_schema() -> None:
    schema = json.loads(render_agent_synthesis_schema())

    assert schema["title"] == "AgentSynthesis"
    assert "summary" in schema["required"]
    assert "why_it_matters" in schema["required"]
    assert set(schema["required"]) == set(schema["properties"])
    assert "project_update" not in json.dumps(schema)


def test_schema_seed_specs_are_basic_memory_schema_notes() -> None:
    specs = schema_seed_specs()

    assert {spec.entity for spec in specs} == {
        "ProjectUpdate",
        "GitHubPullRequestUpdate",
        "GitHubProductionDeployUpdate",
    }
    assert all(spec.metadata["type"] == "schema" for spec in specs)
    assert all(spec.metadata["settings"]["validation"] == "warn" for spec in specs)


def test_parse_github_remote_accepts_https_and_ssh() -> None:
    assert parse_github_remote("https://github.com/basicmachines-co/basic-memory.git") == (
        "basicmachines-co",
        "basic-memory",
    )
    assert parse_github_remote("git@github.com:basicmachines-co/basic-memory.git") == (
        "basicmachines-co",
        "basic-memory",
    )


def test_parse_github_remote_rejects_non_github_remote() -> None:
    with pytest.raises(ValueError, match="GitHub remote"):
        parse_github_remote("https://example.com/basicmachines-co/basic-memory.git")


def test_detect_github_repo_requires_origin_remote(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No remote.origin.url"):
        detect_github_repo(tmp_path)


def test_load_project_update_config_handles_missing_and_invalid_yaml(tmp_path: Path) -> None:
    assert load_project_update_config(tmp_path / "missing.yml") == ProjectUpdateConfig()

    invalid = tmp_path / "invalid.yml"
    invalid.write_text("- not\n- an\n- object\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML object"):
        load_project_update_config(invalid)


def test_private_note_helpers_reject_invalid_repo_shape() -> None:
    context = ProjectUpdateContext(eligible=True, repo="not-owner-repo")
    with pytest.raises(ValueError, match="owner/repo"):
        project_updates._note_directory(context, ProjectUpdateConfig(project="team-memory"))

    missing_repo = ProjectUpdateContext(eligible=True)
    with pytest.raises(ValueError, match="missing repo"):
        project_updates._note_directory(missing_repo, ProjectUpdateConfig(project="team-memory"))


def test_private_note_title_uses_generic_fallback_for_unknown_event() -> None:
    context = ProjectUpdateContext(eligible=True, source_event="unknown")

    assert project_updates._note_title(context) == "Project update"
