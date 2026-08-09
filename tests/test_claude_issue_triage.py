import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TRIAGE_SCRIPT = REPO_ROOT / "scripts" / "edit-issue-labels.sh"
TRIAGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude-issue-triage.yml"


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"

    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("Git for Windows is required to run the triage helper tests")

    git_directory = Path(git_executable).parent
    git_bin_directory = (
        git_directory if git_directory.name.casefold() == "bin" else git_directory.parent / "bin"
    )
    git_bash = git_bin_directory / "bash.exe"
    if not git_bash.is_file():
        raise RuntimeError(f"Git Bash was not found at {git_bash}")
    return str(git_bash)


def _run_triage_helper(
    tmp_path: Path,
    *arguments: str,
    current_labels: tuple[str, ...] = (),
    labels_after_add: tuple[str, ...] | None = None,
    fail_label_read: bool = False,
    fail_label_read_after_add: bool = False,
    fail_label_add: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"issue": {"number": 1205}}), encoding="utf-8")

    gh_arguments_path = tmp_path / "gh-arguments.txt"
    label_add_marker_path = tmp_path / "label-add-completed"
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    gh_path = bin_path / "gh"
    gh_path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ " $* " == *" --method POST "* ]]; then
    printf '%s\n' "__CALL__" "$@" >> "${GH_ARGUMENTS_PATH:?}"
    if [[ "${GH_FAIL_LABEL_ADD:-false}" == "true" ]]; then
        exit 1
    fi
    : > "${GH_LABEL_ADD_MARKER_PATH:?}"
elif [[ " $* " == *" --method DELETE "* ]]; then
    printf '%s\n' "__CALL__" "$@" >> "${GH_ARGUMENTS_PATH:?}"
elif [[ "${GH_FAIL_LABEL_READ:-false}" == "true" ]]; then
    exit 1
elif [[ -f "${GH_LABEL_ADD_MARKER_PATH:?}" ]]; then
    if [[ "${GH_FAIL_LABEL_READ_AFTER_ADD:-false}" == "true" ]]; then
        exit 1
    fi
    printf '%s\n' "${GH_CURRENT_LABELS_AFTER_ADD:-}"
else
    printf '%s\n' "${GH_CURRENT_LABELS:-}"
fi
""",
        encoding="utf-8",
        newline="\n",
    )
    gh_path.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "GH_ARGUMENTS_PATH": gh_arguments_path.as_posix(),
            "GH_CURRENT_LABELS": "\n".join(current_labels),
            "GH_CURRENT_LABELS_AFTER_ADD": "\n".join(
                current_labels if labels_after_add is None else labels_after_add
            ),
            "GH_FAIL_LABEL_ADD": "true" if fail_label_add else "false",
            "GH_FAIL_LABEL_READ": "true" if fail_label_read else "false",
            "GH_FAIL_LABEL_READ_AFTER_ADD": ("true" if fail_label_read_after_add else "false"),
            "GH_LABEL_ADD_MARKER_PATH": label_add_marker_path.as_posix(),
            "GITHUB_EVENT_PATH": event_path.as_posix(),
            "GITHUB_REPOSITORY": "basicmachines-co/basic-memory",
            "PATH": f"{bin_path}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        # Windows' plain bash command launches WSL; select Git Bash for the POSIX helper instead.
        [_bash_executable(), TRIAGE_SCRIPT.as_posix(), *arguments],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    gh_calls: list[list[str]] = []
    for line in (
        gh_arguments_path.read_text(encoding="utf-8").splitlines()
        if gh_arguments_path.exists()
        else []
    ):
        if line == "__CALL__":
            gh_calls.append([])
        else:
            gh_calls[-1].append(line)
    return result, gh_calls


def test_triage_helper_updates_only_owned_labels_without_replacing_other_labels(
    tmp_path: Path,
) -> None:
    result, gh_calls = _run_triage_helper(
        tmp_path,
        "--type",
        "enhancement",
        "--component",
        "none",
        current_labels=("bug", "cloud", "production", "arch-review"),
    )

    assert result.returncode == 0, result.stderr
    assert gh_calls == [
        [
            "api",
            "--method",
            "POST",
            "repos/basicmachines-co/basic-memory/issues/1205/labels",
            "-f",
            "labels[]=enhancement",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/bug",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/cloud",
            "--silent",
        ],
    ]


def test_triage_helper_keeps_only_one_type_and_cloud_component(tmp_path: Path) -> None:
    result, gh_calls = _run_triage_helper(
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
    assert gh_calls == [
        [
            "api",
            "--method",
            "POST",
            "repos/basicmachines-co/basic-memory/issues/1205/labels",
            "-f",
            "labels[]=question",
            "-f",
            "labels[]=cloud",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/bug",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/enhancement",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/documentation",
            "--silent",
        ],
    ]


def test_triage_helper_reconciles_owned_labels_added_after_the_preflight_read(
    tmp_path: Path,
) -> None:
    result, gh_calls = _run_triage_helper(
        tmp_path,
        "--type",
        "enhancement",
        "--component",
        "none",
        current_labels=("bug", "production"),
        labels_after_add=("bug", "documentation", "cloud", "enhancement", "production"),
    )

    assert result.returncode == 0, result.stderr
    assert gh_calls == [
        [
            "api",
            "--method",
            "POST",
            "repos/basicmachines-co/basic-memory/issues/1205/labels",
            "-f",
            "labels[]=enhancement",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/bug",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/documentation",
            "--silent",
        ],
        [
            "api",
            "--method",
            "DELETE",
            "repos/basicmachines-co/basic-memory/issues/1205/labels/cloud",
            "--silent",
        ],
    ]


def test_triage_helper_does_not_update_labels_when_current_labels_cannot_be_read(
    tmp_path: Path,
) -> None:
    result, gh_calls = _run_triage_helper(
        tmp_path,
        "--type",
        "bug",
        "--component",
        "none",
        current_labels=("production",),
        fail_label_read=True,
    )

    assert result.returncode != 0
    assert "unable to read current issue labels" in result.stderr
    assert gh_calls == []


def test_triage_helper_stops_deletes_when_the_reconciliation_read_fails(
    tmp_path: Path,
) -> None:
    result, gh_calls = _run_triage_helper(
        tmp_path,
        "--type",
        "enhancement",
        "--component",
        "none",
        current_labels=("bug", "cloud", "production"),
        fail_label_read_after_add=True,
    )

    assert result.returncode != 0
    assert "unable to reconcile current issue labels" in result.stderr
    assert gh_calls == [
        [
            "api",
            "--method",
            "POST",
            "repos/basicmachines-co/basic-memory/issues/1205/labels",
            "-f",
            "labels[]=enhancement",
            "--silent",
        ]
    ]


def test_triage_helper_does_not_delete_prior_labels_when_add_fails(tmp_path: Path) -> None:
    result, gh_calls = _run_triage_helper(
        tmp_path,
        "--type",
        "enhancement",
        "--component",
        "none",
        current_labels=("bug", "cloud", "production"),
        fail_label_add=True,
    )

    assert result.returncode != 0
    assert gh_calls == [
        [
            "api",
            "--method",
            "POST",
            "repos/basicmachines-co/basic-memory/issues/1205/labels",
            "-f",
            "labels[]=enhancement",
            "--silent",
        ]
    ]


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
    result, gh_calls = _run_triage_helper(tmp_path, *arguments)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert gh_calls == []


def test_triage_workflow_defines_one_semantic_mutation() -> None:
    workflow_text = TRIAGE_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    action_step = workflow["jobs"]["triage"]["steps"][1]
    prompt = action_step["with"]["prompt"]

    assert workflow["concurrency"] == {
        "group": "claude-issue-triage-${{ github.event.issue.number }}",
        "cancel-in-progress": False,
    }
    assert action_step["uses"] == "anthropics/claude-code-action@v1"
    assert "call the triage helper exactly once" in prompt
    assert "--type TYPE --component COMPONENT" in prompt
    assert "Do not probe labels" in prompt
    assert "Priority and complexity are prose assessments only" in prompt
    assert "MCP identifies a component, not a TYPE" in prompt
    assert "MCP tool issue (specific to MCP functionality)" not in prompt
    assert "--add-label" not in workflow_text
