#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "typer>=0.9.0",
# ]
# ///
"""BM Bossbot status and PR-body helpers.

BM Bossbot is a deterministic merge gate — no LLM review. It approves a head
SHA only when the Tests workflow succeeded for it (enforced by the workflow
trigger), the PR is not a draft, the author is trusted, and every review
thread is resolved. Code review itself comes from the Codex connector and
human reviewers; this gate just refuses to let unaddressed feedback merge.
"""

from __future__ import annotations

import json
import os
import re
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


def count_unresolved_review_threads(*, token: str, repo: str, number: int) -> int:
    """Count unresolved review threads (e.g. open Codex inline comments) on a PR.

    Review threads are the canonical 'outstanding feedback' signal: bot reviewers
    submit COMMENTED reviews that never flip reviewDecision, so thread resolution
    is the only deterministic way to know feedback was addressed.
    """
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise SystemExit(f"Invalid repository: {repo}")

    query = """
    query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes { isResolved }
          }
        }
      }
    }
    """

    unresolved = 0
    cursor: str | None = None
    while True:
        response = _github_request(
            method="POST",
            path="/graphql",
            token=token,
            payload={
                "query": query,
                "variables": {"owner": owner, "name": name, "number": number, "cursor": cursor},
            },
        )
        if not isinstance(response, Mapping) or response.get("errors"):
            raise SystemExit(f"GitHub GraphQL reviewThreads query failed: {response}")
        try:
            threads = response["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = threads["nodes"]
            page_info = threads["pageInfo"]
        except (KeyError, TypeError):
            raise SystemExit(
                "GitHub GraphQL reviewThreads response was missing expected fields"
            ) from None
        unresolved += sum(1 for node in nodes if not node.get("isResolved"))
        if not page_info.get("hasNextPage"):
            return unresolved
        cursor = page_info.get("endCursor")


def unresolved_threads_result(count: int) -> ApprovalResult:
    return ApprovalResult(
        False,
        "failure",
        f"BM Bossbot found {count} unresolved review thread(s)",
    )


def evaluate_gate(
    *,
    token: str,
    repo: str,
    number: int,
    trusted: bool,
) -> tuple[ApprovalResult, int]:
    """Deterministic approval decision: trusted author + zero unresolved threads.

    Tests-passed-for-this-head and non-draft are enforced upstream by the
    workflow trigger and the normalize step (should_review). Returns the
    result plus the unresolved-thread count for the PR-body summary.
    """
    if not trusted:
        return (
            ApprovalResult(
                False,
                "failure",
                "BM Bossbot only gates owner/member/collaborator PRs",
            ),
            0,
        )
    unresolved = count_unresolved_review_threads(token=token, repo=repo, number=number)
    if unresolved > 0:
        return unresolved_threads_result(unresolved), unresolved
    return ApprovalResult(True, "success", APPROVED_DESCRIPTION), 0


def build_status_payload(*, state: str, description: str, target_url: str) -> dict[str, str]:
    return {
        "state": state,
        "context": STATUS_CONTEXT,
        "description": description,
        "target_url": target_url,
    }


def render_summary(
    *,
    head_sha: str,
    result: ApprovalResult,
    trusted: bool,
    unresolved_threads: int,
) -> str:
    return "\n".join(
        [
            f"Reviewed SHA: `{head_sha}`",
            "Gate: deterministic (tests, draft, author trust, review threads)",
            f"Status: `{result.state}` - {result.description}",
            "",
            f"- Trusted author: {'yes' if trusted else 'no'}",
            f"- Unresolved review threads: {unresolved_threads}",
            "",
            "Code review comes from the Codex connector and human reviewers;",
            "resolve every review thread to (re)gain approval for this head SHA.",
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
    trusted: bool,
    repo: str | None,
    run_url: str,
    token_env: str,
) -> ApprovalResult:
    event = pull_request_event(read_json(event_path), repo_override=repo)
    token = _token(token_env)

    result, unresolved = evaluate_gate(
        token=token, repo=event.repo, number=event.number, trusted=trusted
    )
    current_body = get_pull_request_body(token=token, repo=event.repo, number=event.number)
    updated_body = upsert_summary_block(
        current_body,
        render_summary(
            head_sha=event.head_sha,
            result=result,
            trusted=trusted,
            unresolved_threads=unresolved,
        ),
    )
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


def get_pull_request_head_sha(*, token: str, repo: str, number: int) -> str:
    response = _github_request(
        method="GET",
        path=f"/repos/{repo}/pulls/{number}",
        token=token,
    )
    if not isinstance(response, Mapping):
        raise SystemExit("GitHub API response for pull request was invalid")
    head = response.get("head")
    head_sha = _string(head.get("sha")) if isinstance(head, Mapping) else ""
    if not head_sha:
        raise SystemExit("GitHub API response was missing pull request head SHA")
    return head_sha


def head_sha_was_approved(*, token: str, repo: str, sha: str) -> bool:
    """Return whether a full BM Bossbot review previously approved this head SHA.

    Commit statuses are append-only history, so the approval record survives a
    later thread-failure status for the same SHA. The recheck path can post a
    new status on every review-thread event, so a busy PR can accumulate more
    than one page of statuses — page through all of them or the approval
    record falls off page one and a valid approval is never restored.
    """
    page = 1
    while True:
        response = _github_request(
            method="GET",
            path=f"/repos/{repo}/commits/{sha}/statuses?per_page=100&page={page}",
            token=token,
        )
        if not isinstance(response, list):
            raise SystemExit("GitHub API response for commit statuses was invalid")
        if not response:
            return False
        if any(
            isinstance(status, Mapping)
            and status.get("context") == STATUS_CONTEXT
            and status.get("state") == "success"
            and status.get("description") == APPROVED_DESCRIPTION
            for status in response
        ):
            return True
        page += 1


def recheck_threads(
    *,
    repo: str,
    number: int,
    run_url: str,
    token_env: str,
) -> None:
    """Re-evaluate the approval status when review threads change.

    Trigger: pull_request_review / review_comment / review_thread events.
    Why: the full review runs once per head SHA after Tests; feedback that
         arrives later (or gets resolved later) must move the gate without
         re-running the LLM review.
    Outcome: unresolved threads flip the status to failure; once every thread
         is resolved, a previously earned approval for the same head SHA is
         restored. Without a prior approval the status is left untouched so a
         pending/failed review cannot be upgraded by thread resolution alone.
    """
    token = _token(token_env)
    head_sha = get_pull_request_head_sha(token=token, repo=repo, number=number)
    unresolved = count_unresolved_review_threads(token=token, repo=repo, number=number)

    if unresolved > 0:
        result = unresolved_threads_result(unresolved)
    elif head_sha_was_approved(token=token, repo=repo, sha=head_sha):
        result = ApprovalResult(True, "success", APPROVED_DESCRIPTION)
    else:
        typer.echo(
            f"All review threads resolved but no prior approval exists for {head_sha}; "
            "leaving status unchanged"
        )
        return

    set_commit_status(
        token=token,
        repo=repo,
        sha=head_sha,
        payload=build_status_payload(
            state=result.state,
            description=result.description,
            target_url=run_url,
        ),
    )
    typer.echo(f"Marked {STATUS_CONTEXT} {result.state} for {head_sha} ({result.description})")


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
    trusted: Annotated[
        str,
        typer.Option(
            "--trusted",
            help="Whether the PR author is trusted (true/false from the classify step).",
        ),
    ],
    run_url: Annotated[str, typer.Option("--run-url", help="Workflow run URL.")],
    repo: Annotated[str | None, typer.Option("--repo", help="owner/name repository.")] = None,
    token_env: Annotated[
        str,
        typer.Option("--token-env", help="Environment variable containing a GitHub token."),
    ] = "GITHUB_TOKEN",
) -> None:
    """Finalize BM Bossbot Approval from the deterministic gate."""
    result = finalize_review(
        event_path=event,
        trusted=trusted.strip().lower() == "true",
        repo=repo,
        run_url=run_url,
        token_env=token_env,
    )
    if not result.approved:
        raise typer.Exit(1)


@app.command("recheck")
def recheck(
    pr_number: Annotated[int, typer.Option("--pr-number", min=1, help="Pull request number.")],
    run_url: Annotated[str, typer.Option("--run-url", help="Workflow run URL.")],
    repo: Annotated[str, typer.Option("--repo", help="owner/name repository.")],
    token_env: Annotated[
        str,
        typer.Option("--token-env", help="Environment variable containing a GitHub token."),
    ] = "GITHUB_TOKEN",
) -> None:
    """Re-evaluate BM Bossbot Approval from current review-thread state."""
    recheck_threads(repo=repo, number=pr_number, run_url=run_url, token_env=token_env)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
