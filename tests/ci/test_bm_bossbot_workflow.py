import re
from pathlib import Path

import yaml


WORKFLOW_PATH = Path(".github/workflows/bm-bossbot.yml")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_bm_bossbot_runs_after_successful_tests_workflow() -> None:
    workflow = _workflow()
    review_job = workflow["jobs"]["review"]

    assert workflow["name"] == "BM Bossbot"
    assert "pull_request_target" not in workflow["on"]
    assert workflow["on"]["workflow_run"]["workflows"] == ["Tests"]
    assert workflow["on"]["workflow_run"]["types"] == ["completed"]
    assert "workflow_dispatch" in workflow["on"]
    assert "github.event.workflow_run.conclusion == 'success'" in review_job["if"]
    assert "github.event.workflow_run.pull_requests[0].number != ''" in review_job["if"]
    assert review_job["outputs"]["should_review"] == "${{ steps.pr.outputs.should_review }}"

    permissions = workflow["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["pull-requests"] == "write"
    assert permissions["statuses"] == "write"

    assert "assets" not in workflow["jobs"]


def test_bm_bossbot_workflow_never_checks_out_untrusted_head() -> None:
    workflow = _workflow()
    checkout_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == "actions/checkout@v6"
    ]

    assert checkout_steps
    for checkout_step in checkout_steps:
        assert checkout_step["with"]["ref"] == "${{ github.event.repository.default_branch }}"
        assert "${{ github.event.pull_request.head.sha }}" not in str(checkout_step)
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # The danger is consuming UNTRUSTED PR data: checking out the PR head, or
    # interpolating attacker-controlled strings (title/body/branch names) into
    # run scripts. The numeric PR id is safe and the recheck job needs it, so
    # allow exactly `.number` and nothing else from the pull_request payload.
    pr_event_fields = set(re.findall(r"github\.event\.pull_request\.[a-zA-Z_.]+", workflow_text))
    assert pr_event_fields <= {"github.event.pull_request.number"}
    assert "github.event.pull_request.head" not in workflow_text
    assert "cancel-in-progress: true" in workflow_text


def test_bm_bossbot_workflow_has_deterministic_status_steps() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["review"]["steps"]
    names = [step["name"] for step in steps]

    assert "Set up uv" in names
    assert "Mark BM Bossbot approval pending" in names
    assert "Finalize BM Bossbot approval" in names
    # The gate is deterministic: no LLM review step and no image generation.
    assert "Run BM Bossbot review with Codex" not in names
    assert "Collect sanitized PR context" not in names

    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "openai/codex-action" not in workflow_text
    assert "OPENAI_API_KEY" not in workflow_text

    pending = next(step for step in steps if step["name"] == "Mark BM Bossbot approval pending")
    assert pending["if"] == "steps.pr.outputs.should_review == 'true'"
    finalize = next(step for step in steps if step["name"] == "Finalize BM Bossbot approval")
    assert finalize["if"] == "always() && steps.pr.outputs.should_review == 'true'"
    assert '--trusted "${{ steps.trust.outputs.trusted_author }}"' in finalize["run"]
    assert "BM Bossbot Approval" in workflow_text
    assert "uv run --script scripts/bm_bossbot_status.py pending" in workflow_text
    assert "uv run --script scripts/bm_bossbot_status.py finalize" in workflow_text


def test_bm_bossbot_rejects_stale_successful_test_runs_before_finalize() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _workflow()
    steps = workflow["jobs"]["review"]["steps"]
    normalize = next(step for step in steps if step["name"] == "Normalize PR event")
    classify = next(step for step in steps if step["name"] == "Classify PR author")

    assert "tested_sha" in normalize["run"]
    assert "current_head_sha" in normalize["run"]
    assert "actions/workflows/test.yml/runs" in normalize["run"]
    assert "-f event=push" in normalize["run"]
    assert "-f event=pull_request" not in normalize["run"]
    assert '-f head_sha="${current_head_sha}"' in normalize["run"]
    assert 'select(.conclusion == "success")' in normalize["run"]
    assert "no successful Tests workflow for ${current_head_sha}" in workflow_text
    stale_sha_guard = '[ -n "${tested_sha}" ] && [ "${tested_sha}" != "${current_head_sha}" ]'
    assert stale_sha_guard in normalize["run"]
    assert "should_review=false" in normalize["run"]
    assert (
        "Tests passed for ${tested_sha}, but current head is ${current_head_sha}" in workflow_text
    )
    assert classify["if"] == "steps.pr.outputs.should_review == 'true'"


def test_bm_bossbot_has_no_image_generation() -> None:
    """The per-PR image job was removed: it spent OpenAI tokens on every run."""
    workflow = _workflow()
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "assets" not in workflow["jobs"]
    assert "generate_pr_infographic" not in workflow_text
    assert "pr-assets/" not in workflow_text


def test_bm_bossbot_classifies_authors_and_gates_untrusted_deterministically() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["review"]["steps"]

    classify = next(step for step in steps if step["name"] == "Classify PR author")
    finalize = next(step for step in steps if step["name"] == "Finalize BM Bossbot approval")

    assert "OWNER|MEMBER|COLLABORATOR" in classify["run"]
    assert classify["if"] == "steps.pr.outputs.should_review == 'true'"
    assert '--trusted "${{ steps.trust.outputs.trusted_author }}"' in finalize["run"]


def test_claude_code_review_is_manual_advisory_only() -> None:
    workflow = yaml.safe_load(
        Path(".github/workflows/claude-code-review.yml").read_text(encoding="utf-8")
    )

    assert "pull_request" not in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["pr_number"]["required"] is True
