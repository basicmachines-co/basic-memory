#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "typer>=0.9.0",
# ]
# ///
"""BM Bossbot status and PR-body helpers.

The workflow lets Codex write a structured review. This script owns the
deterministic gate: only a complete review for the current head SHA can publish
the required success status.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Mapping

import typer


STATUS_CONTEXT = "BM Bossbot Approval"
SUMMARY_START = "<!-- BM_BOSSBOT_SUMMARY:start -->"
SUMMARY_END = "<!-- BM_BOSSBOT_SUMMARY:end -->"
APPROVED_DESCRIPTION = "BM Bossbot approved this head SHA"
PENDING_DESCRIPTION = "BM Bossbot is reviewing this head SHA"
app = typer.Typer(
    add_completion=False,
    help="Manage deterministic BM Bossbot PR approval statuses.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class ApprovalResult:
    approved: bool
    state: str
    description: str


@dataclass(frozen=True)
class PullRequestEvent:
    repo: str
    number: int
    head_sha: str
    body: str


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing JSON file: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from None


def pull_request_event(
    payload: Mapping[str, Any], repo_override: str | None = None
) -> PullRequestEvent:
    pr = payload.get("pull_request")
    if not isinstance(pr, Mapping):
        raise SystemExit("GitHub event payload is missing pull_request")

    repo = repo_override
    if repo is None:
        repository = payload.get("repository")
        if isinstance(repository, Mapping):
            repo = _string(repository.get("full_name"))
    if not repo:
        raise SystemExit("Could not determine GitHub repository")

    number = pr.get("number")
    if not isinstance(number, int):
        raise SystemExit("GitHub event payload is missing pull_request.number")

    head = pr.get("head")
    head_sha = (
        _string(head.get("sha")) if isinstance(head, Mapping) else _string(pr.get("head_sha"))
    )
    if not head_sha:
        raise SystemExit("GitHub event payload is missing pull_request.head.sha")

    return PullRequestEvent(
        repo=repo,
        number=number,
        head_sha=head_sha,
        body=_string(pr.get("body")),
    )


def validate_review(payload: Mapping[str, Any], *, expected_head_sha: str) -> ApprovalResult:
    required = {
        "reviewed_head_sha",
        "review_complete",
        "verdict",
        "blocking_findings",
        "nonblocking_findings",
        "summary",
    }
    if not required.issubset(payload):
        return ApprovalResult(False, "failure", "BM Bossbot review output was invalid")

    if payload["reviewed_head_sha"] != expected_head_sha:
        return ApprovalResult(False, "failure", "BM Bossbot reviewed a stale head SHA")

    if payload["review_complete"] is not True:
        return ApprovalResult(False, "failure", "BM Bossbot review did not finish")

    verdict = payload["verdict"]
    if verdict not in {"approve", "changes_requested", "needs_human"}:
        return ApprovalResult(False, "failure", "BM Bossbot review output was invalid")

    blockers = payload["blocking_findings"]
    if not isinstance(blockers, list):
        return ApprovalResult(False, "failure", "BM Bossbot review output was invalid")

    if verdict != "approve" or blockers:
        return ApprovalResult(False, "failure", "BM Bossbot requested changes")

    return ApprovalResult(True, "success", APPROVED_DESCRIPTION)


def build_status_payload(*, state: str, description: str, target_url: str) -> dict[str, str]:
    return {
        "state": state,
        "context": STATUS_CONTEXT,
        "description": description,
        "target_url": target_url,
    }


def render_summary(review: Mapping[str, Any], result: ApprovalResult) -> str:
    blockers = _format_findings(review.get("blocking_findings"))
    nonblockers = _format_findings(review.get("nonblocking_findings"))
    summary = _string(review.get("summary")) or "No summary provided."
    return "\n".join(
        [
            f"Reviewed SHA: `{_string(review.get('reviewed_head_sha')) or 'unknown'}`",
            f"Verdict: `{_string(review.get('verdict')) or 'invalid'}`",
            f"Status: `{result.state}` - {result.description}",
            "",
            "Summary:",
            summary,
            "",
            "Blocking findings:",
            blockers,
            "",
            "Non-blocking findings:",
            nonblockers,
        ]
    )


def upsert_summary_block(body: str, summary: str) -> str:
    block = f"{SUMMARY_START}\n{summary.rstrip()}\n{SUMMARY_END}"
    pattern = re.compile(
        rf"{re.escape(SUMMARY_START)}.*?{re.escape(SUMMARY_END)}",
        flags=re.DOTALL,
    )
    if pattern.search(body):
        return pattern.sub(block, body, count=1)
    if body.strip():
        return f"{body.rstrip()}\n\n{block}\n"
    return f"{block}\n"


def set_commit_status(*, token: str, repo: str, sha: str, payload: Mapping[str, str]) -> None:
    _github_request(
        method="POST",
        path=f"/repos/{repo}/statuses/{sha}",
        token=token,
        payload=payload,
    )


def update_pull_request_body(*, token: str, repo: str, number: int, body: str) -> None:
    _github_request(
        method="PATCH",
        path=f"/repos/{repo}/pulls/{number}",
        token=token,
        payload={"body": body},
    )


def get_pull_request_body(*, token: str, repo: str, number: int) -> str:
    response = _github_request(
        method="GET",
        path=f"/repos/{repo}/pulls/{number}",
        token=token,
    )
    if not isinstance(response, Mapping):
        raise SystemExit("GitHub API response for pull request was invalid")
    return _string(response.get("body"))


def mark_pending(
    *,
    event_path: Path,
    repo: str | None,
    run_url: str,
    token_env: str,
) -> None:
    event = pull_request_event(read_json(event_path), repo_override=repo)
    set_commit_status(
        token=_token(token_env),
        repo=event.repo,
        sha=event.head_sha,
        payload=build_status_payload(
            state="pending",
            description=PENDING_DESCRIPTION,
            target_url=run_url,
        ),
    )
    typer.echo(f"Marked {STATUS_CONTEXT} pending for {event.head_sha}")


def finalize_review(
    *,
    event_path: Path,
    review_path: Path,
    repo: str | None,
    run_url: str,
    token_env: str,
) -> ApprovalResult:
    event = pull_request_event(read_json(event_path), repo_override=repo)
    token = _token(token_env)

    review: Mapping[str, Any]
    try:
        raw_review = read_json(review_path)
        if not isinstance(raw_review, Mapping):
            raw_review = {}
        review = raw_review
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        review = {}

    result = validate_review(review, expected_head_sha=event.head_sha)
    current_body = get_pull_request_body(token=token, repo=event.repo, number=event.number)
    updated_body = upsert_summary_block(current_body, render_summary(review, result))
    update_pull_request_body(token=token, repo=event.repo, number=event.number, body=updated_body)
    set_commit_status(
        token=token,
        repo=event.repo,
        sha=event.head_sha,
        payload=build_status_payload(
            state=result.state,
            description=result.description,
            target_url=run_url,
        ),
    )
    typer.echo(f"Marked {STATUS_CONTEXT} {result.state} for {event.head_sha}")
    return result


def _github_request(
    *,
    method: str,
    path: str,
    token: str,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "basic-memory-bm-bossbot",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API request failed: {exc.code} {detail}") from None
    return json.loads(response_body) if response_body else None


def _format_findings(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "- None"
    lines: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            title = _string(item.get("title")) or _string(item.get("summary")) or "Finding"
            body = _string(item.get("body")) or _string(item.get("details"))
            lines.append(f"- {title}: {body}" if body else f"- {title}")
        else:
            lines.append(f"- {_string(item)}")
    return "\n".join(lines)


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _token(env_name: str) -> str:
    token = os.environ.get(env_name)
    if not token:
        raise SystemExit(f"Missing required token environment variable: {env_name}")
    return token


@app.command("pending")
def pending(
    event: Annotated[
        Path,
        typer.Option(
            "--event",
            exists=True,
            dir_okay=False,
            readable=True,
            help="GitHub event payload JSON.",
        ),
    ],
    run_url: Annotated[str, typer.Option("--run-url", help="Workflow run URL.")],
    repo: Annotated[str | None, typer.Option("--repo", help="owner/name repository.")] = None,
    token_env: Annotated[
        str,
        typer.Option("--token-env", help="Environment variable containing a GitHub token."),
    ] = "GITHUB_TOKEN",
) -> None:
    """Set BM Bossbot Approval pending on the PR head SHA."""
    mark_pending(event_path=event, repo=repo, run_url=run_url, token_env=token_env)


@app.command("finalize")
def finalize(
    event: Annotated[
        Path,
        typer.Option(
            "--event",
            exists=True,
            dir_okay=False,
            readable=True,
            help="GitHub event payload JSON.",
        ),
    ],
    review: Annotated[
        Path,
        typer.Option(
            "--review",
            dir_okay=False,
            help="Structured BM Bossbot review JSON.",
        ),
    ],
    run_url: Annotated[str, typer.Option("--run-url", help="Workflow run URL.")],
    repo: Annotated[str | None, typer.Option("--repo", help="owner/name repository.")] = None,
    token_env: Annotated[
        str,
        typer.Option("--token-env", help="Environment variable containing a GitHub token."),
    ] = "GITHUB_TOKEN",
) -> None:
    """Finalize BM Bossbot Approval from a structured review JSON file."""
    result = finalize_review(
        event_path=event,
        review_path=review,
        repo=repo,
        run_url=run_url,
        token_env=token_env,
    )
    if not result.approved:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
