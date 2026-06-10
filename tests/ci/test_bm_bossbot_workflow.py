from pathlib import Path

import yaml


WORKFLOW_PATH = Path(".github/workflows/bm-bossbot.yml")
PROMPT_PATH = Path(".github/basic-memory/bm-bossbot-review.md")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_bm_bossbot_uses_safe_pull_request_target_gate() -> None:
    workflow = _workflow()

    assert workflow["name"] == "BM Bossbot"
    assert "pull_request_target" in workflow["on"]
    assert workflow["on"]["pull_request_target"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
    assert "workflow_dispatch" in workflow["on"]

    permissions = workflow["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["pull-requests"] == "write"
    assert permissions["statuses"] == "write"

    asset_permissions = workflow["jobs"]["assets"]["permissions"]
    assert asset_permissions["contents"] == "write"
    assert asset_permissions["pull-requests"] == "write"


def test_bm_bossbot_workflow_never_checks_out_untrusted_head() -> None:
    workflow = _workflow()
    review_job = workflow["jobs"]["review"]
    steps = review_job["steps"]
    checkout_step = next(step for step in steps if step.get("uses") == "actions/checkout@v6")

    assert checkout_step["with"]["ref"] == "${{ github.event.pull_request.base.ref || github.ref }}"
    assert "${{ github.event.pull_request.head.sha }}" not in str(checkout_step)
    assert "cancel-in-progress: true" in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_bm_bossbot_workflow_has_deterministic_status_steps() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["review"]["steps"]
    names = [step["name"] for step in steps]

    assert "Set up uv" in names
    assert "Mark BM Bossbot approval pending" in names
    assert "Run BM Bossbot review with Codex" in names
    assert "Finalize BM Bossbot approval" in names

    run_codex = next(step for step in steps if step["name"] == "Run BM Bossbot review with Codex")
    assert run_codex["uses"] == "openai/codex-action@v1"
    assert run_codex["with"]["openai-api-key"] == "${{ secrets.OPENAI_API_KEY }}"
    assert "--output-schema" in run_codex["with"]["codex-args"]

    finalize = next(step for step in steps if step["name"] == "Finalize BM Bossbot approval")
    assert finalize["if"] == "always()"
    assert "BM Bossbot Approval" in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "uv run --script scripts/bm_bossbot_status.py pending" in WORKFLOW_PATH.read_text(
        encoding="utf-8"
    )
    assert "uv run --script scripts/bm_bossbot_status.py finalize" in WORKFLOW_PATH.read_text(
        encoding="utf-8"
    )


def test_bm_bossbot_assets_are_non_gating_and_separate_from_review_job() -> None:
    workflow = _workflow()
    review_steps = workflow["jobs"]["review"]["steps"]
    asset_job = workflow["jobs"]["assets"]
    asset_steps = asset_job["steps"]

    assert asset_job["needs"] == "review"
    assert asset_job["if"] == "needs.review.result == 'success'"
    assert not any(step["name"] == "Generate non-gating PR infographic" for step in review_steps)
    assert not any(step["name"] == "Publish non-gating PR infographic" for step in review_steps)

    generate = next(step for step in asset_steps if step["name"] == "Generate non-gating PR infographic")
    publish = next(step for step in asset_steps if step["name"] == "Publish non-gating PR infographic")

    assert generate["continue-on-error"] is True
    assert publish["continue-on-error"] is True
    assert "uv run --script scripts/generate_pr_infographic.py" in WORKFLOW_PATH.read_text(
        encoding="utf-8"
    )
    assert "--provenance-output" in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "git rm -rf --ignore-unmatch ." in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "<!-- pr-infographic:start -->" in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "gh pr edit" in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "--body-file" in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "BM_INFOGRAPHIC_PROVENANCE:start" in WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "BM_INFOGRAPHIC_PROVENANCE:end" in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_bm_bossbot_rejects_oversized_diffs_without_partial_approval() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _workflow()
    steps = workflow["jobs"]["review"]["steps"]
    run_codex = next(step for step in steps if step["name"] == "Run BM Bossbot review with Codex")

    assert "max_diff_bytes=120000" in workflow_text
    assert "diff_truncated=true" in workflow_text
    assert "review_complete: false" in workflow_text
    assert 'verdict: "needs_human"' in workflow_text
    assert "Diff exceeds BM Bossbot review limit" in workflow_text
    assert (
        run_codex["if"]
        == "steps.trust.outputs.trusted_author == 'true' && steps.context.outputs.diff_truncated != 'true'"
    )
    assert "head -c 120000" not in workflow_text


def test_bm_bossbot_does_not_run_codex_for_outside_contributors() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _workflow()
    steps = workflow["jobs"]["review"]["steps"]

    classify = next(step for step in steps if step["name"] == "Classify PR author")
    outside = next(step for step in steps if step["name"] == "Decline outside contributor PRs")
    collect = next(step for step in steps if step["name"] == "Collect sanitized PR context")
    run_codex = next(step for step in steps if step["name"] == "Run BM Bossbot review with Codex")
    select_review = next(step for step in steps if step["name"] == "Select BM Bossbot review output")
    finalize = next(step for step in steps if step["name"] == "Finalize BM Bossbot approval")

    assert "OWNER|MEMBER|COLLABORATOR" in classify["run"]
    assert outside["if"] == "steps.trust.outputs.trusted_author != 'true'"
    assert collect["if"] == "steps.trust.outputs.trusted_author == 'true'"
    assert (
        run_codex["if"]
        == "steps.trust.outputs.trusted_author == 'true' && steps.context.outputs.diff_truncated != 'true'"
    )
    assert select_review["if"] == "always()"
    assert "BM Bossbot does not run for outside contributors" in workflow_text
    assert "missing-bm-bossbot-review.json" in workflow_text
    assert '--review "${{ steps.review_output.outputs.review_file }}"' in finalize["run"]


def test_bm_bossbot_prompt_references_engineering_style_and_json_bullets() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "docs/ENGINEERING_STYLE.md" in prompt
    assert "- Set `reviewed_head_sha`" in prompt
    assert "- Do not include Markdown outside the JSON." in prompt


def test_claude_code_review_is_manual_advisory_only() -> None:
    workflow = yaml.safe_load(
        Path(".github/workflows/claude-code-review.yml").read_text(encoding="utf-8")
    )

    assert "pull_request" not in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["pr_number"]["required"] is True
