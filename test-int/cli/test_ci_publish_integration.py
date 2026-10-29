"""`bm ci publish` against a real project: it writes the update note, and a
second publish with the same idempotency key updates that note in place."""

import json
from pathlib import Path

from typer.testing import CliRunner, Result

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


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


def _publish(tmp_path: Path, project: str) -> Result:
    """Collect a merged-PR event and publish its synthesis into `project`."""
    event_path = _write_pr_event(tmp_path / "event.json")
    context_path = tmp_path / "context.json"
    config_path = tmp_path / "config.yml"
    synthesis_path = tmp_path / "synthesis.json"
    config_path.write_text(f"project: {project}\n", encoding="utf-8")
    collected = runner.invoke(
        cli_app,
        [
            "ci", "collect", "--event-name", "pull_request", "--event-path", str(event_path),
            "--config", str(config_path), "--output", str(context_path),
        ],
    )  # fmt: skip
    assert collected.exit_code == 0, collected.output
    synthesis_path.write_text(json.dumps(_synthesis_payload(repo="evil/repo")), encoding="utf-8")
    return runner.invoke(
        cli_app,
        [
            "ci", "publish", "--config", str(config_path), "--context", str(context_path),
            "--synthesis", str(synthesis_path),
        ],
    )  # fmt: skip


def test_publish_command_writes_project_update_note(
    app, test_project, config_manager, tmp_path: Path
) -> None:
    result = _publish(tmp_path, test_project.name)

    assert result.exit_code == 0, result.output
    notes = list(Path(test_project.path).rglob("*.md"))
    update = next(note for note in notes if "type: project_update" in note.read_text())
    text = update.read_text()
    # The repository comes from the event, never from the model's synthesis.
    assert "repo: basicmachines-co/demo" in text
    assert "source_event: pull_request_merged" in text
    assert "idempotency_key: github:basicmachines-co/demo:pull_request_merged:7" in text


def test_publish_command_updates_the_note_with_the_same_idempotency_key(
    app, test_project, config_manager, tmp_path: Path
) -> None:
    key = "github:basicmachines-co/demo:pull_request_merged:7"
    written = runner.invoke(
        cli_app,
        [
            "tool", "write", "--identifier", "custom/project-updates/existing-pr-update.md",
            "--content", "Earlier summary.", "--note-type", "project_update",
            "--metadata", json.dumps({"idempotency_key": key}), "--project", test_project.name,
        ],
    )  # fmt: skip
    assert written.exit_code == 0, written.output

    result = _publish(tmp_path, test_project.name)

    assert result.exit_code == 0, result.output
    existing = Path(test_project.path) / "custom/project-updates/existing-pr-update.md"
    assert "Earlier summary." not in existing.read_text()
    assert len(list(Path(test_project.path).rglob("*.md"))) == 1
