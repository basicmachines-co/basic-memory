import json
from pathlib import Path
from typing import Mapping

import pytest
from typer.testing import CliRunner

from scripts import bm_bossbot_status


def _event_payload(body: str = "Event snapshot body") -> dict[str, object]:
    return {
        "repository": {"full_name": "basicmachines-co/basic-memory"},
        "pull_request": {
            "number": 925,
            "body": body,
            "head": {"sha": "abc123"},
        },
    }


def test_status_script_is_uv_typer_entrypoint() -> None:
    source = bm_bossbot_status.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()

    assert text.startswith("#!/usr/bin/env -S uv run --script\n")
    assert "# /// script" in text
    assert "typer" in text
    assert hasattr(bm_bossbot_status, "app")


def _review_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "reviewed_head_sha": "abc123",
        "review_complete": True,
        "verdict": "approve",
        "blocking_findings": [],
        "nonblocking_findings": [],
        "summary": "The change is ready.",
    }
    payload.update(overrides)
    return payload


def test_validate_review_accepts_matching_approved_head_sha() -> None:
    result = bm_bossbot_status.validate_review(_review_payload(), expected_head_sha="abc123")

    assert result.approved is True
    assert result.state == "success"
    assert result.description == "BM Bossbot approved this head SHA"


def test_validate_review_rejects_stale_head_sha() -> None:
    result = bm_bossbot_status.validate_review(_review_payload(), expected_head_sha="def456")

    assert result.approved is False
    assert result.state == "failure"
    assert result.description == "BM Bossbot reviewed a stale head SHA"


def test_validate_review_rejects_blocking_findings() -> None:
    result = bm_bossbot_status.validate_review(
        _review_payload(blocking_findings=[{"title": "Missing test", "body": "Add coverage."}]),
        expected_head_sha="abc123",
    )

    assert result.approved is False
    assert result.state == "failure"
    assert result.description == "BM Bossbot requested changes"


def test_status_payload_uses_required_context() -> None:
    payload = bm_bossbot_status.build_status_payload(
        state="pending",
        description="BM Bossbot is reviewing this head SHA",
        target_url="https://github.com/basicmachines-co/basic-memory/actions/runs/1",
    )

    assert payload == {
        "state": "pending",
        "context": "BM Bossbot Approval",
        "description": "BM Bossbot is reviewing this head SHA",
        "target_url": "https://github.com/basicmachines-co/basic-memory/actions/runs/1",
    }


def test_upsert_summary_block_replaces_existing_block() -> None:
    body = "\n".join(
        [
            "Intro",
            "<!-- BM_BOSSBOT_SUMMARY:start -->",
            "Old summary",
            "<!-- BM_BOSSBOT_SUMMARY:end -->",
            "Footer",
        ]
    )

    updated = bm_bossbot_status.upsert_summary_block(body, "New summary")

    assert "Old summary" not in updated
    assert "New summary" in updated
    assert updated.startswith("Intro")
    assert updated.endswith("Footer")


def test_finalize_review_fetches_current_pr_body_before_upserting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "event.json"
    review_path = tmp_path / "review.json"
    event_path.write_text(json.dumps(_event_payload()), encoding="utf-8")
    review_path.write_text(json.dumps(_review_payload()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    updated_bodies: list[str] = []
    statuses: list[Mapping[str, str]] = []

    def fake_get_pull_request_body(*, token: str, repo: str, number: int) -> str:
        assert token == "token"
        assert repo == "basicmachines-co/basic-memory"
        assert number == 925
        return "Current body edited while the workflow was running"

    def fake_update_pull_request_body(*, token: str, repo: str, number: int, body: str) -> None:
        updated_bodies.append(body)

    def fake_set_commit_status(
        *,
        token: str,
        repo: str,
        sha: str,
        payload: Mapping[str, str],
    ) -> None:
        statuses.append(payload)

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_body", fake_get_pull_request_body)
    monkeypatch.setattr(
        bm_bossbot_status, "update_pull_request_body", fake_update_pull_request_body
    )
    monkeypatch.setattr(bm_bossbot_status, "set_commit_status", fake_set_commit_status)
    monkeypatch.setattr(bm_bossbot_status, "count_unresolved_review_threads", lambda **_: 0)

    result = bm_bossbot_status.finalize_review(
        event_path=event_path,
        review_path=review_path,
        repo=None,
        run_url="https://github.com/basicmachines-co/basic-memory/actions/runs/1",
        token_env="GITHUB_TOKEN",
    )

    assert result.approved is True
    assert "Current body edited while the workflow was running" in updated_bodies[0]
    assert "Event snapshot body" not in updated_bodies[0]
    assert statuses[0]["state"] == "success"


def test_finalize_review_blocks_approval_on_unresolved_review_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "event.json"
    review_path = tmp_path / "review.json"
    event_path.write_text(json.dumps(_event_payload()), encoding="utf-8")
    review_path.write_text(json.dumps(_review_payload()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    statuses: list[Mapping[str, str]] = []

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_body", lambda **_: "Body")
    monkeypatch.setattr(bm_bossbot_status, "update_pull_request_body", lambda **_: None)
    monkeypatch.setattr(
        bm_bossbot_status,
        "set_commit_status",
        lambda *, token, repo, sha, payload: statuses.append(payload),
    )
    monkeypatch.setattr(bm_bossbot_status, "count_unresolved_review_threads", lambda **_: 2)

    result = bm_bossbot_status.finalize_review(
        event_path=event_path,
        review_path=review_path,
        repo=None,
        run_url="https://github.com/basicmachines-co/basic-memory/actions/runs/1",
        token_env="GITHUB_TOKEN",
    )

    assert result.approved is False
    assert result.state == "failure"
    assert result.description == "BM Bossbot found 2 unresolved review thread(s)"
    assert statuses[0]["state"] == "failure"


def test_finalize_review_skips_thread_count_when_review_already_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "event.json"
    review_path = tmp_path / "review.json"
    event_path.write_text(json.dumps(_event_payload()), encoding="utf-8")
    review_path.write_text(
        json.dumps(_review_payload(verdict="changes_requested")), encoding="utf-8"
    )
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_body", lambda **_: "Body")
    monkeypatch.setattr(bm_bossbot_status, "update_pull_request_body", lambda **_: None)
    monkeypatch.setattr(bm_bossbot_status, "set_commit_status", lambda **_: None)

    def fail_count(**_: object) -> int:
        raise AssertionError("thread count must not run when the review already failed")

    monkeypatch.setattr(bm_bossbot_status, "count_unresolved_review_threads", fail_count)

    result = bm_bossbot_status.finalize_review(
        event_path=event_path,
        review_path=review_path,
        repo=None,
        run_url="https://github.com/basicmachines-co/basic-memory/actions/runs/1",
        token_env="GITHUB_TOKEN",
    )

    assert result.approved is False
    assert result.description == "BM Bossbot requested changes"


def test_count_unresolved_review_threads_pages_through_graphql_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "CUR"},
                            "nodes": [{"isResolved": False}, {"isResolved": True}],
                        }
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{"isResolved": False}],
                        }
                    }
                }
            }
        },
    ]
    cursors: list[object] = []

    def fake_github_request(
        *, method: str, path: str, token: str, payload: Mapping[str, object] | None = None
    ) -> object:
        assert method == "POST"
        assert path == "/graphql"
        assert payload is not None
        variables = payload["variables"]
        assert isinstance(variables, Mapping)
        cursors.append(variables["cursor"])
        return pages.pop(0)

    monkeypatch.setattr(bm_bossbot_status, "_github_request", fake_github_request)

    count = bm_bossbot_status.count_unresolved_review_threads(
        token="token", repo="basicmachines-co/basic-memory", number=925
    )

    assert count == 2
    assert cursors == [None, "CUR"]


def test_recheck_marks_failure_when_threads_are_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    statuses: list[Mapping[str, str]] = []

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_head_sha", lambda **_: "abc123")
    monkeypatch.setattr(bm_bossbot_status, "count_unresolved_review_threads", lambda **_: 3)
    monkeypatch.setattr(
        bm_bossbot_status,
        "set_commit_status",
        lambda *, token, repo, sha, payload: statuses.append({"sha": sha, **payload}),
    )

    bm_bossbot_status.recheck_threads(
        repo="basicmachines-co/basic-memory",
        number=925,
        run_url="https://github.com/basicmachines-co/basic-memory/actions/runs/2",
        token_env="GITHUB_TOKEN",
    )

    assert statuses[0]["sha"] == "abc123"
    assert statuses[0]["state"] == "failure"
    assert "3 unresolved review thread(s)" in statuses[0]["description"]


def test_recheck_restores_prior_approval_when_threads_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    statuses: list[Mapping[str, str]] = []

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_head_sha", lambda **_: "abc123")
    monkeypatch.setattr(bm_bossbot_status, "count_unresolved_review_threads", lambda **_: 0)
    monkeypatch.setattr(bm_bossbot_status, "head_sha_was_approved", lambda **_: True)
    monkeypatch.setattr(
        bm_bossbot_status,
        "set_commit_status",
        lambda *, token, repo, sha, payload: statuses.append(payload),
    )

    bm_bossbot_status.recheck_threads(
        repo="basicmachines-co/basic-memory",
        number=925,
        run_url="https://github.com/basicmachines-co/basic-memory/actions/runs/2",
        token_env="GITHUB_TOKEN",
    )

    assert statuses[0]["state"] == "success"
    assert statuses[0]["description"] == "BM Bossbot approved this head SHA"


def test_recheck_leaves_status_alone_without_prior_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_head_sha", lambda **_: "abc123")
    monkeypatch.setattr(bm_bossbot_status, "count_unresolved_review_threads", lambda **_: 0)
    monkeypatch.setattr(bm_bossbot_status, "head_sha_was_approved", lambda **_: False)

    def fail_set_status(**_: object) -> None:
        raise AssertionError("status must not change without a prior approval")

    monkeypatch.setattr(bm_bossbot_status, "set_commit_status", fail_set_status)

    bm_bossbot_status.recheck_threads(
        repo="basicmachines-co/basic-memory",
        number=925,
        run_url="https://github.com/basicmachines-co/basic-memory/actions/runs/2",
        token_env="GITHUB_TOKEN",
    )


def test_head_sha_was_approved_matches_only_the_approval_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [
        {
            "context": "BM Bossbot Approval",
            "state": "failure",
            "description": "BM Bossbot found 2 unresolved review thread(s)",
        },
        {
            "context": "BM Bossbot Approval",
            "state": "success",
            "description": "BM Bossbot approved this head SHA",
        },
        {"context": "license/cla", "state": "success", "description": "ok"},
    ]
    monkeypatch.setattr(bm_bossbot_status, "_github_request", lambda **_: history)

    assert (
        bm_bossbot_status.head_sha_was_approved(
            token="token", repo="basicmachines-co/basic-memory", sha="abc123"
        )
        is True
    )

    monkeypatch.setattr(bm_bossbot_status, "_github_request", lambda **_: history[:1])
    assert (
        bm_bossbot_status.head_sha_was_approved(
            token="token", repo="basicmachines-co/basic-memory", sha="abc123"
        )
        is False
    )


def test_finalize_cli_marks_failure_when_review_file_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "event.json"
    missing_review_path = tmp_path / "missing-review.json"
    event_path.write_text(json.dumps(_event_payload(body="Current body")), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    updated_bodies: list[str] = []
    statuses: list[Mapping[str, str]] = []

    def fake_get_pull_request_body(*, token: str, repo: str, number: int) -> str:
        return "Current body"

    def fake_update_pull_request_body(*, token: str, repo: str, number: int, body: str) -> None:
        updated_bodies.append(body)

    def fake_set_commit_status(
        *,
        token: str,
        repo: str,
        sha: str,
        payload: Mapping[str, str],
    ) -> None:
        statuses.append(payload)

    monkeypatch.setattr(bm_bossbot_status, "get_pull_request_body", fake_get_pull_request_body)
    monkeypatch.setattr(
        bm_bossbot_status, "update_pull_request_body", fake_update_pull_request_body
    )
    monkeypatch.setattr(bm_bossbot_status, "set_commit_status", fake_set_commit_status)

    result = CliRunner().invoke(
        bm_bossbot_status.app,
        [
            "finalize",
            "--event",
            str(event_path),
            "--review",
            str(missing_review_path),
            "--repo",
            "basicmachines-co/basic-memory",
            "--run-url",
            "https://github.com/basicmachines-co/basic-memory/actions/runs/1",
        ],
    )

    assert result.exit_code == 1
    assert "BM Bossbot review output was invalid" in updated_bodies[0]
    assert statuses[0]["state"] == "failure"
