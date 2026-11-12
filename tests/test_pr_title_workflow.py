from pathlib import Path

import yaml
from typing import Any


def _semantic_pr_action_inputs() -> dict[str, Any]:
    workflow = yaml.safe_load(Path(".github/workflows/pr-title.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["main"]["steps"]
    action_step = next(
        step for step in steps if step.get("uses") == "amannn/action-semantic-pull-request@v6"
    )
    return action_step["with"]


def test_pr_title_workflow_accepts_any_scope() -> None:
    """A fixed scope list blocked PRs for new areas such as docs(man); nothing reads the scope."""
    inputs = _semantic_pr_action_inputs()

    assert "scopes" not in inputs
    assert inputs["requireScope"] is False


def test_pr_title_workflow_still_checks_the_type() -> None:
    types = _semantic_pr_action_inputs()["types"].split()

    assert {"feat", "fix", "docs", "chore", "ci"} <= set(types)
