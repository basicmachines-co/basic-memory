import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TRIAGE_SCRIPT = REPO_ROOT / "scripts" / "edit-issue-labels.sh"
TRIAGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude-issue-triage.yml"


def _run_triage_helper(
    tmp_path: Path,
    *arguments: str,
    current_labels: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"issue": {"number": 1205}}), encoding="utf-8")

    gh_arguments_path = tmp_path / "gh-arguments.txt"
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    gh_path = bin_path / "gh"
    gh_path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ " $* " == *" --method PUT "* ]]; then
    printf '%s\n' "$@" > "${GH_ARGUMENTS_PATH:?}"
else
    printf '%s\n' "${GH_CURRENT_LABELS:-}"
fi
""",
        encoding="utf-8",
    )
    gh_path.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "GH_ARGUMENTS_PATH": str(gh_arguments_path),
            "GH_CURRENT_LABELS": "\n".join(current_labels),
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "basicmachines-co/basic-memory",
            "PATH": f"{bin_path}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(TRIAGE_SCRIPT), *arguments],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    gh_arguments = (
        gh_arguments_path.read_text(encoding="utf-8").splitlines()
        if gh_arguments_path.exists()
        else []
    )
    return result, gh_arguments


def test_triage_helper_replaces_owned_labels_and_preserves_other_labels(tmp_path: Path) -> None:
    result, gh_arguments = _run_triage_helper(
        tmp_path,
        "--type",
        "enhancement",
        "--component",
        "none",
        current_labels=("bug", "cloud", "production", "arch-review"),
    )

    assert result.returncode == 0, result.stderr
    assert gh_arguments == [
        "api",
        "--method",
        "PUT",
        "repos/basicmachines-co/basic-memory/issues/1205/labels",
        "-f",
        "labels[]=production",
        "-f",
        "labels[]=arch-review",
        "-f",
        "labels[]=enhancement",
        "--silent",
    ]


def test_triage_helper_keeps_only_one_type_and_cloud_component(tmp_path: Path) -> None:
    result, gh_arguments = _run_triage_helper(
        tmp_path,
        "--type",
        "question",
        "--component",
        "cloud",
        current_labels=(
            "bug",
            "enhancement",
            "documentation",
            "question",
            "cloud",
            "production",
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "labels[]=bug" not in gh_arguments
    assert "labels[]=enhancement" not in gh_arguments
    assert "labels[]=documentation" not in gh_arguments
    assert gh_arguments.count("labels[]=question") == 1
    assert gh_arguments.count("labels[]=cloud") == 1
    assert "labels[]=production" in gh_arguments


@pytest.mark.parametrize(
    "arguments, expected_error",
    [
        (("--add-label", "bug"), "only --type and --component are accepted"),
        (("--component", "none"), "--type is required"),
        (("--type", "bug"), "--component is required"),
        (
            ("--type", "bug", "--type", "enhancement", "--component", "none"),
            "--type may be provided only once",
        ),
        (("--type", "feature", "--component", "none"), "unsupported triage type"),
        (("--type", "bug", "--component", "database"), "unsupported triage component"),
    ],
)
def test_triage_helper_rejects_probe_and_unsupported_arguments(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_error: str,
) -> None:
    result, gh_arguments = _run_triage_helper(tmp_path, *arguments)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert gh_arguments == []


def test_triage_workflow_defines_one_semantic_mutation() -> None:
    workflow_text = TRIAGE_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    action_step = workflow["jobs"]["triage"]["steps"][1]
    prompt = action_step["with"]["prompt"]

    assert action_step["uses"] == "anthropics/claude-code-action@v1"
    assert "call the triage helper exactly once" in prompt
    assert "--type TYPE --component COMPONENT" in prompt
    assert "Do not probe labels" in prompt
    assert "Priority and complexity are prose assessments only" in prompt
    assert "--add-label" not in workflow_text
