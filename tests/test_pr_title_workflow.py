from pathlib import Path

import yaml


def _semantic_pr_action_inputs() -> dict:
    workflow = yaml.safe_load(Path(".github/workflows/pr-title.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["main"]["steps"]
    action_step = next(
        step for step in steps if step.get("uses") == "amannn/action-semantic-pull-request@v6"
    )
    return action_step["with"]


def test_pr_title_workflow_allows_ci_scope() -> None:
    scopes = _semantic_pr_action_inputs()["scopes"].split()

    assert "ci" in scopes
