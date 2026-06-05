"""Project update capture helpers for GitHub Actions.

Auto BM treats GitHub as the immutable source layer, the agent as the synthesis
layer, and Basic Memory as the durable project-memory layer.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


PULL_REQUEST_MERGED = "pull_request_merged"
PRODUCTION_DEPLOY_SUCCEEDED = "production_deploy_succeeded"
DEFAULT_NOTE_FOLDER_TEMPLATE = "project-updates/github/{owner}/{repo}"
DEFAULT_CONFIG_PATH = ".github/basic-memory/config.yml"
DEFAULT_WORKFLOW_PATH = ".github/workflows/basic-memory.yml"
DEFAULT_PROMPT_PATH = ".github/basic-memory/memory-ci-capture.md"
DEFAULT_CONTEXT_PATH = ".github/basic-memory/project-update-context.json"


class ProjectUpdateConfig(BaseModel):
    """Non-secret Auto BM repository configuration."""

    project: str | None = None
    project_id: str | None = None
    workspace: str | None = None
    deploy_workflows: list[str] = Field(default_factory=lambda: ["Deploy Production"])
    production_environments: list[str] = Field(default_factory=lambda: ["production"])
    note_folder: str = DEFAULT_NOTE_FOLDER_TEMPLATE
    codex_model: str | None = None
    codex_effort: str | None = None

    @field_validator("deploy_workflows", "production_environments")
    @classmethod
    def _non_empty_list(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-empty value")
        return cleaned


class ChangedFile(BaseModel):
    """A GitHub pull request file summary."""

    filename: str
    status: str | None = None
    additions: int | None = None
    deletions: int | None = None
    changes: int | None = None


class CommitSummary(BaseModel):
    """A compact GitHub pull request commit summary."""

    sha: str | None = None
    message: str | None = None
    author: str | None = None


class LinkedIssueDetail(BaseModel):
    """GitHub issue context referenced by a pull request."""

    number: int
    title: str | None = None
    body_excerpt: str | None = None
    state: str | None = None
    url: str | None = None


class ProjectUpdateContext(BaseModel):
    """Normalized facts collected from a GitHub event payload."""

    eligible: bool
    source: Literal["github"] = "github"
    source_event: str | None = None
    skip_reason: str | None = None
    repo: str | None = None
    repo_url: str | None = None
    source_url: str | None = None
    occurred_at: str | None = None
    idempotency_key: str | None = None
    sha: str | None = None
    pr_number: int | None = None
    workflow_run_id: str | None = None
    environment: str | None = None
    title: str | None = None
    body: str | None = None
    author: str | None = None
    labels: list[str] = Field(default_factory=list)
    linked_issues: list[str] = Field(default_factory=list)
    linked_issue_details: list[LinkedIssueDetail] = Field(default_factory=list)
    changed_files: list[ChangedFile] = Field(default_factory=list)
    changed_files_count: int | None = None
    commits: list[CommitSummary] = Field(default_factory=list)


class AgentSynthesis(BaseModel):
    """Agent-authored synthesis for a project update."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    story: str
    problem_addressed: str
    solution: str
    system_impact: str
    why_it_matters: str
    components_changed: list[str] = Field(default_factory=list)
    complexity_introduced: list[str] = Field(default_factory=list)
    refactors_or_removals: list[str] = Field(default_factory=list)
    user_facing_changes: list[str] = Field(default_factory=list)
    internal_changes: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    decision_candidates: list[str] = Field(default_factory=list)
    task_candidates: list[str] = Field(default_factory=list)

    @field_validator(
        "summary",
        "story",
        "problem_addressed",
        "solution",
        "system_impact",
        "why_it_matters",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped


class ProjectUpdateNote(BaseModel):
    """Final note payload for the Basic Memory writer."""

    title: str
    directory: str
    content: str
    metadata: dict[str, Any]
    tags: list[str]


class SchemaSeedSpec(BaseModel):
    """Basic Memory schema note seed."""

    title: str
    entity: str
    content: str
    metadata: dict[str, Any]


def parse_github_remote(remote_url: str) -> tuple[str, str]:
    """Parse an HTTPS or SSH GitHub remote into owner/repo."""
    patterns = (
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, remote_url.strip())
        if match:
            return match.group("owner"), match.group("repo")
    raise ValueError(f"Expected a GitHub remote, got: {remote_url}")


def detect_github_repo(cwd: Path) -> tuple[str, str]:
    """Detect the GitHub origin for a repository checkout."""
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("No remote.origin.url found; run this from a GitHub repository checkout")
    return parse_github_remote(result.stdout.strip())


def load_project_update_config(path: Path) -> ProjectUpdateConfig:
    """Load Auto BM repository config from YAML."""
    if not path.exists():
        return ProjectUpdateConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return ProjectUpdateConfig.model_validate(raw)


def write_project_update_config(path: Path, config: ProjectUpdateConfig) -> None:
    """Write repository config YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude_none=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _load_event_payload(event_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"GitHub event payload not found: {event_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"GitHub event payload is not valid JSON: {event_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("GitHub event payload must be a JSON object")
    return payload


def collect_project_update_context(
    *,
    event_name: str,
    event_path: Path,
    config: ProjectUpdateConfig,
) -> ProjectUpdateContext:
    """Normalize a GitHub Actions event into a project update context."""
    payload = _load_event_payload(event_path)
    if event_name == "pull_request":
        return _collect_pull_request_context(payload)
    if event_name == "workflow_run":
        return _collect_workflow_run_context(payload, config)
    return ProjectUpdateContext(
        eligible=False,
        skip_reason=f"unsupported GitHub event: {event_name}",
    )


def _repo_fields(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    repo = payload.get("repository")
    if not isinstance(repo, dict):
        return None, None
    full_name = repo.get("full_name")
    html_url = repo.get("html_url")
    return (
        full_name if isinstance(full_name, str) else None,
        html_url if isinstance(html_url, str) else None,
    )


def _label_names(labels: Any) -> list[str]:
    if not isinstance(labels, list):
        return []
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return names


def _linked_issues(*texts: str | None) -> list[str]:
    seen: set[str] = set()
    issues: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in re.finditer(r"(?<![\w/])#(?P<number>\d+)\b", text):
            issue = f"#{match.group('number')}"
            if issue not in seen:
                seen.add(issue)
                issues.append(issue)
    return issues


def _github_api_get(path: str, token: str) -> list[Any] | dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "basic-memory-ci",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"GitHub API request failed ({exc.code}) for {path}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"GitHub API request failed for {path}: {exc.reason}") from exc
    if not isinstance(payload, (list, dict)):
        raise ValueError(f"GitHub API response for {path} must be a JSON object or array")
    return payload


def _github_api_get_list(path: str, token: str) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        payload = _github_api_get(f"{path}{separator}per_page=100&page={page}", token)
        if not isinstance(payload, list):
            raise ValueError(f"GitHub API response for {path} must be a JSON array")
        items.extend(payload)
        if len(payload) < 100:
            return items
        page += 1


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _body_excerpt(value: Any, *, limit: int = 2000) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 15].rstrip() + "... [truncated]"


def _changed_file_from_github(raw: Any) -> ChangedFile | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("filename"), str):
        return None
    return ChangedFile(
        filename=raw["filename"],
        status=_text_or_none(raw.get("status")),
        additions=_int_or_none(raw.get("additions")),
        deletions=_int_or_none(raw.get("deletions")),
        changes=_int_or_none(raw.get("changes")),
    )


def _commit_summary_from_github(raw: Any) -> CommitSummary | None:
    if not isinstance(raw, dict):
        return None
    commit = raw.get("commit")
    if not isinstance(commit, dict):
        return None
    author = commit.get("author")
    return CommitSummary(
        sha=_text_or_none(raw.get("sha")),
        message=_text_or_none(commit.get("message")),
        author=_text_or_none(author.get("name")) if isinstance(author, dict) else None,
    )


def _issue_number(issue: str) -> int | None:
    match = re.fullmatch(r"#(?P<number>\d+)", issue)
    return int(match.group("number")) if match else None


def _issue_detail_from_github(raw: Any) -> LinkedIssueDetail | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
        return None
    return LinkedIssueDetail(
        number=raw["number"],
        title=_text_or_none(raw.get("title")),
        body_excerpt=_body_excerpt(raw.get("body")),
        state=_text_or_none(raw.get("state")),
        url=_text_or_none(raw.get("html_url")),
    )


def _enrich_pull_request_context(context: ProjectUpdateContext) -> ProjectUpdateContext:
    token = os.environ.get("GITHUB_TOKEN")
    if not token or not context.repo or context.pr_number is None:
        return context

    files = [
        file
        for file in (
            _changed_file_from_github(raw)
            for raw in _github_api_get_list(
                f"/repos/{context.repo}/pulls/{context.pr_number}/files", token
            )
        )
        if file is not None
    ]
    commits = [
        commit
        for commit in (
            _commit_summary_from_github(raw)
            for raw in _github_api_get_list(
                f"/repos/{context.repo}/pulls/{context.pr_number}/commits", token
            )
        )
        if commit is not None
    ]
    issue_details: list[LinkedIssueDetail] = []
    for issue in context.linked_issues:
        number = _issue_number(issue)
        if number is None:
            continue
        detail = _issue_detail_from_github(
            _github_api_get(f"/repos/{context.repo}/issues/{number}", token)
        )
        if detail is not None:
            issue_details.append(detail)

    return context.model_copy(
        update={
            "changed_files": files,
            "commits": commits,
            "linked_issue_details": issue_details,
        }
    )


def _collect_pull_request_context(payload: dict[str, Any]) -> ProjectUpdateContext:
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return ProjectUpdateContext(eligible=False, skip_reason="pull request payload missing")

    if payload.get("action") != "closed":
        return ProjectUpdateContext(
            eligible=False, skip_reason="pull request action was not closed"
        )

    if pr.get("merged") is not True:
        return ProjectUpdateContext(
            eligible=False,
            skip_reason="pull request was closed without merging",
        )

    repo, repo_url = _repo_fields(payload)
    number = pr.get("number")
    title = pr.get("title") if isinstance(pr.get("title"), str) else None
    body = pr.get("body") if isinstance(pr.get("body"), str) else None
    source_url = pr.get("html_url") if isinstance(pr.get("html_url"), str) else None
    sha = pr.get("merge_commit_sha") if isinstance(pr.get("merge_commit_sha"), str) else None
    occurred_at = pr.get("merged_at") if isinstance(pr.get("merged_at"), str) else None
    author = None
    user = pr.get("user")
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        author = user["login"]

    idempotency_key = None
    if repo and isinstance(number, int):
        idempotency_key = f"github:{repo}:{PULL_REQUEST_MERGED}:{number}"

    context = ProjectUpdateContext(
        eligible=True,
        source_event=PULL_REQUEST_MERGED,
        repo=repo,
        repo_url=repo_url,
        source_url=source_url,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        sha=sha,
        pr_number=number if isinstance(number, int) else None,
        title=title,
        body=body,
        author=author,
        labels=_label_names(pr.get("labels")),
        linked_issues=_linked_issues(title, body),
        changed_files_count=(
            pr["changed_files"] if isinstance(pr.get("changed_files"), int) else None
        ),
    )
    return _enrich_pull_request_context(context)


def _collect_workflow_run_context(
    payload: dict[str, Any],
    config: ProjectUpdateConfig,
) -> ProjectUpdateContext:
    run = payload.get("workflow_run")
    if not isinstance(run, dict):
        return ProjectUpdateContext(eligible=False, skip_reason="workflow run payload missing")

    conclusion = run.get("conclusion")
    if conclusion != "success":
        return ProjectUpdateContext(
            eligible=False,
            skip_reason=f"workflow conclusion was {conclusion or 'missing'}",
        )

    workflow_name = run.get("name")
    if workflow_name not in config.deploy_workflows:
        return ProjectUpdateContext(
            eligible=False,
            skip_reason=f"workflow '{workflow_name}' is not configured for project updates",
        )

    repo, repo_url = _repo_fields(payload)
    run_id = run.get("id")
    workflow_run_id = str(run_id) if run_id is not None else None
    environment = config.production_environments[0]
    source_url = run.get("html_url") if isinstance(run.get("html_url"), str) else None
    sha = run.get("head_sha") if isinstance(run.get("head_sha"), str) else None
    occurred_at = run.get("updated_at") if isinstance(run.get("updated_at"), str) else None

    idempotency_key = None
    if repo and workflow_run_id:
        idempotency_key = (
            f"github:{repo}:{PRODUCTION_DEPLOY_SUCCEEDED}:{environment}:{workflow_run_id}"
        )

    return ProjectUpdateContext(
        eligible=True,
        source_event=PRODUCTION_DEPLOY_SUCCEEDED,
        repo=repo,
        repo_url=repo_url,
        source_url=source_url,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        sha=sha,
        workflow_run_id=workflow_run_id,
        environment=environment,
        title=str(workflow_name) if workflow_name is not None else None,
    )


def _owner_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise ValueError(f"Repository must be owner/repo, got: {repo}")
    owner, repo_name = repo.split("/", 1)
    return owner, repo_name


def _note_directory(context: ProjectUpdateContext, config: ProjectUpdateConfig | None) -> str:
    if not context.repo:
        raise ValueError("Project update context is missing repo")
    owner, repo_name = _owner_repo(context.repo)
    template = config.note_folder if config else DEFAULT_NOTE_FOLDER_TEMPLATE
    return template.format(owner=owner, repo=repo_name, full_repo=context.repo)


def _note_title(context: ProjectUpdateContext) -> str:
    if context.source_event == PULL_REQUEST_MERGED and context.pr_number:
        title = context.title or "Merged pull request"
        return f"PR #{context.pr_number}: {title}"
    if context.source_event == PRODUCTION_DEPLOY_SUCCEEDED:
        environment = context.environment or "production"
        when = (context.occurred_at or "").split("T", 1)[0] or "unknown date"
        return f"{environment.title()} deploy: {when}"
    return "Project update"


def build_project_update_note(
    *,
    context: ProjectUpdateContext,
    synthesis: AgentSynthesis,
    config: ProjectUpdateConfig | None = None,
) -> ProjectUpdateNote:
    """Build the final Basic Memory project update note."""
    if not context.eligible:
        raise ValueError(f"Cannot build a note for an ineligible context: {context.skip_reason}")
    if not context.source_event or not context.repo or not context.idempotency_key:
        raise ValueError("Project update context is missing deterministic identity fields")

    metadata: dict[str, Any] = {
        "source": "github",
        "source_event": context.source_event,
        "repo": context.repo,
        "source_url": context.source_url,
        "occurred_at": context.occurred_at,
        "idempotency_key": context.idempotency_key,
        "sha": context.sha,
        "pr_number": context.pr_number,
        "workflow_run_id": context.workflow_run_id,
        "environment": context.environment,
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}

    sections = [
        f"# {_note_title(context)}",
        "## Summary",
        synthesis.summary,
        "## Story",
        synthesis.story,
        "## Problem Addressed",
        synthesis.problem_addressed,
        "## How The Change Solves It",
        synthesis.solution,
        "## Impact On The System",
        synthesis.system_impact,
        "## Project Memory",
        synthesis.why_it_matters,
    ]

    _extend_list_section(sections, "Components Changed", synthesis.components_changed)
    _extend_list_section(sections, "Complexity Introduced", synthesis.complexity_introduced)
    _extend_list_section(sections, "Refactors Or Removals", synthesis.refactors_or_removals)
    _extend_list_section(sections, "User-Facing Changes", synthesis.user_facing_changes)
    _extend_list_section(sections, "Internal Changes", synthesis.internal_changes)
    _extend_list_section(sections, "Verification", synthesis.verification)
    _extend_list_section(sections, "Follow-Ups", synthesis.follow_ups)
    _extend_list_section(sections, "Decision Candidates", synthesis.decision_candidates)
    _extend_list_section(sections, "Task Candidates", synthesis.task_candidates)

    source_links = []
    if context.source_url:
        source_links.append(f"- Source: {context.source_url}")
    if context.repo_url:
        source_links.append(f"- Repository: {context.repo_url}")
    if context.linked_issues:
        source_links.append(f"- Linked issues: {', '.join(context.linked_issues)}")
    if source_links:
        sections.extend(["## Source Links", *source_links])

    observations = [
        f"- [summary] {synthesis.summary}",
        f"- [impact] {synthesis.system_impact}",
        f"- [source] GitHub {context.source_event} in {context.repo}",
    ]
    sections.extend(["## Observations", *observations])

    return ProjectUpdateNote(
        title=_note_title(context),
        directory=_note_directory(context, config),
        content="\n\n".join(sections).strip() + "\n",
        metadata=metadata,
        tags=["github", "project-update", context.source_event],
    )


def _extend_list_section(sections: list[str], title: str, values: list[str]) -> None:
    cleaned = [value.strip() for value in values if value.strip()]
    if cleaned:
        sections.extend([f"## {title}", *[f"- {value}" for value in cleaned]])


def render_agent_synthesis_schema() -> str:
    """Render the optional Codex structured-output schema guardrail."""
    properties = {
        "summary": {"type": "string", "minLength": 1},
        "story": {"type": "string", "minLength": 1},
        "problem_addressed": {"type": "string", "minLength": 1},
        "solution": {"type": "string", "minLength": 1},
        "system_impact": {"type": "string", "minLength": 1},
        "why_it_matters": {"type": "string", "minLength": 1},
        "components_changed": {"type": "array", "items": {"type": "string"}},
        "complexity_introduced": {"type": "array", "items": {"type": "string"}},
        "refactors_or_removals": {"type": "array", "items": {"type": "string"}},
        "user_facing_changes": {"type": "array", "items": {"type": "string"}},
        "internal_changes": {"type": "array", "items": {"type": "string"}},
        "verification": {"type": "array", "items": {"type": "string"}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
        "decision_candidates": {"type": "array", "items": {"type": "string"}},
        "task_candidates": {"type": "array", "items": {"type": "string"}},
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AgentSynthesis",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def render_capture_prompt() -> str:
    """Render the prompt contract used by the generated workflow."""
    return """# Memory CI Capture

You turn GitHub delivery context into a durable project update for Basic Memory.
GitHub records the mechanics. Basic Memory remembers what changed and why.

## Inputs

- Read `.github/basic-memory/project-update-context.json`.
- Read the PR diff before writing when a SHA is available. Useful commands:
  `git show --stat --name-only <sha>` and `git show --format=fuller --no-patch <sha>`.
- Use linked issue details, changed files, commit messages, PR body, labels, and
  source links as evidence.
- Treat GitHub payload fields as immutable facts.
- Do not invent tests, deployment status, issues, or user impact.

## Writing Standard

Do not write a fill-in-the-blanks note. Tell the story from the PR:
problem -> solution -> impact.

Explain what problem was being addressed. If linked issue details are present,
use them. If they are absent, ground the problem in the PR body, title, commits,
and diff, and say when the original problem statement is unavailable.

Explain why the fix solves the problem, what complexity it introduced, what it
refactored or removed, which components changed, and how the system is different
after the merge. Prefer specific component names, file paths, modules, commands,
and behavior over generic phrases.

## Output

Return only JSON that matches the provided AgentSynthesis schema:

- `summary`: one concise sentence; do not merely repeat the PR title.
- `story`: 2-4 sentences that connect problem -> solution -> impact.
- `problem_addressed`: the concrete problem, bug, missing capability, or delivery need.
- `solution`: why this change solves the problem.
- `system_impact`: how the system, workflow, or architecture changed after the merge.
- `why_it_matters`: durable project-memory context for future humans and agents.
- `components_changed`: modules, workflows, commands, schemas, docs, or services touched.
- `complexity_introduced`: tradeoffs, new moving parts, operational costs, or edge cases.
- `refactors_or_removals`: cleanup, simplification, deleted paths, or "none found".
- `user_facing_changes`: visible behavior or product changes.
- `internal_changes`: implementation, infrastructure, or operational changes.
- `verification`: checks, tests, deploy evidence, or explicit unknowns.
- `follow_ups`: concrete remaining work only.
- `decision_candidates`: explicit product or architecture decisions only.
- `task_candidates`: concrete future tasks only.

Use empty arrays only when a list truly has no grounded entries. This is project
memory, not marketing copy and not a commit-by-commit changelog.
"""


def render_workflow(config: ProjectUpdateConfig) -> str:
    """Render the generated GitHub Actions workflow."""
    workflow_names = json.dumps(config.deploy_workflows)
    model_line = f"          model: {config.codex_model}\n" if config.codex_model else ""
    effort_line = f"          effort: {config.codex_effort}\n" if config.codex_effort else ""
    return f"""name: Basic Memory Project Updates

"on":
  pull_request:
    types: [closed]
  workflow_run:
    workflows: {workflow_names}
    types: [completed]

jobs:
  project-update:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: read
      issues: read
      actions: read
    steps:
      - name: Checkout repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"

      - name: Install Basic Memory
        run: |
          python -m pip install --upgrade pip
          pip install basic-memory

      - name: Collect project update context
        id: collect
        env:
          GITHUB_TOKEN: ${{{{ github.token }}}}
        run: |
          bm ci collect \\
            --config {DEFAULT_CONFIG_PATH} \\
            --output {DEFAULT_CONTEXT_PATH}

      - name: Stop when event is not eligible
        if: steps.collect.outputs.eligible != 'true'
        run: |
          echo "Auto BM skipped: ${{{{ steps.collect.outputs.skip_reason }}}}"

      - name: Write Codex output schema
        if: steps.collect.outputs.eligible == 'true'
        run: |
          bm ci agent-schema --output "${{{{ runner.temp }}}}/agent-synthesis.schema.json"

      - name: Synthesize project update with Codex
        if: steps.collect.outputs.eligible == 'true'
        uses: openai/codex-action@v1
        with:
          openai-api-key: ${{{{ secrets.OPENAI_API_KEY }}}}
          prompt-file: {DEFAULT_PROMPT_PATH}
          output-file: ${{{{ runner.temp }}}}/agent-synthesis.json
          output-schema-file: ${{{{ runner.temp }}}}/agent-synthesis.schema.json
          sandbox: read-only
          safety-strategy: drop-sudo
{model_line}{effort_line}
      - name: Publish project update
        if: steps.collect.outputs.eligible == 'true'
        env:
          BASIC_MEMORY_CLOUD_API_KEY: ${{{{ secrets.BASIC_MEMORY_API_KEY }}}}
          BASIC_MEMORY_CI_CLOUD_HOST: ${{{{ vars.BASIC_MEMORY_CLOUD_HOST }}}}
        run: |
          if [ -n "$BASIC_MEMORY_CI_CLOUD_HOST" ]; then
            export BASIC_MEMORY_CLOUD_HOST="$BASIC_MEMORY_CI_CLOUD_HOST"
          fi
          bm ci publish \\
            --cloud \\
            --config {DEFAULT_CONFIG_PATH} \\
            --context {DEFAULT_CONTEXT_PATH} \\
            --synthesis "${{{{ runner.temp }}}}/agent-synthesis.json"
"""


def schema_seed_specs() -> list[SchemaSeedSpec]:
    """Return Basic Memory schema note seeds for Auto BM project updates."""
    return [
        _schema_seed(
            title="ProjectUpdate",
            entity="ProjectUpdate",
            schema={
                "summary": "string, concise account of what changed",
                "story": "string, narrative connecting problem -> solution -> impact",
                "problem_addressed": "string, concrete problem or delivery need",
                "solution": "string, why the change solves the problem",
                "system_impact": "string, impact on system behavior or architecture",
                "why_it_matters": "string, durable context for future humans and agents",
                "components_changed": "array, modules, workflows, commands, or services touched",
                "complexity_introduced": "array, tradeoffs or new moving parts",
                "refactors_or_removals": "array, cleanup, simplification, or deleted paths",
                "source": "string, source system such as github",
                "source_event": ("string, pull_request_merged or production_deploy_succeeded"),
                "repo": "string, owner/repository",
                "source_url": "string, canonical source URL",
                "occurred_at?": "string, ISO timestamp",
                "idempotency_key": "string, stable source identity",
                "sha?": "string, commit SHA",
                "pr_number?": "integer, pull request number",
                "workflow_run_id?": "string, GitHub Actions workflow run id",
                "environment?": "string, deployment environment",
            },
            body=(
                "A ProjectUpdate preserves what changed in a project and the durable "
                "context future readers need. "
                "It should tell the delivery story: the problem, why the solution worked, "
                "what components changed, what complexity or cleanup followed, and the "
                "impact on the system. GitHub records mechanics; Basic Memory keeps the "
                "durable narrative."
            ),
        ),
        _schema_seed(
            title="GitHubPullRequestUpdate",
            entity="GitHubPullRequestUpdate",
            schema={
                "intent": "string, purpose of the merged pull request",
                "problem_addressed": "string, issue, bug, missing capability, or workflow pain",
                "solution": "string, why this implementation solves the problem",
                "system_impact": "string, behavior, architecture, or workflow impact",
                "changed_area?(array)": "string, product or implementation areas touched",
                "components_changed?(array)": "string, modules, workflows, commands, or docs touched",
                "complexity_introduced?(array)": "string, tradeoffs or new moving parts",
                "refactors_or_removals?(array)": "string, cleanup, simplification, or deleted paths",
                "linked_issue?(array)": "string, issues closed or advanced",
                "verification?(array)": "string, checks and tests observed",
                "follow_up?(array)": "string, concrete remaining work",
            },
            body=(
                "Guidance for pull request project updates: preserve the story behind "
                "the PR, not just the title. Explain the problem, the fix, why it works, "
                "changed components, tradeoffs, cleanup, issue links, and verification. "
                "Do not summarize commit by commit unless that is the clearest explanation."
            ),
        ),
        _schema_seed(
            title="GitHubProductionDeployUpdate",
            entity="GitHubProductionDeployUpdate",
            schema={
                "deployed_sha": "string, deployed commit SHA",
                "environment": "string, production environment",
                "workflow_run_id": "string, GitHub Actions workflow run id",
                "story": "string, what changed since the previous production deploy",
                "system_impact": "string, production impact on behavior or operations",
                "components_changed?(array)": "string, deployed modules, services, or workflows",
                "complexity_introduced?(array)": "string, operational tradeoffs or new moving parts",
                "verification?(array)": "string, deploy evidence and smoke checks",
                "user_impact?(array)": "string, user-facing impact since previous deploy",
                "rollback_note?": "string, rollback or mitigation note when known",
            },
            body=(
                "Guidance for production deploy project updates: preserve what actually "
                "reached production, the durable context, the deployed SHA, environment, "
                "workflow run, changed components, operational complexity, and "
                "verification evidence. Do not overclaim beyond the source facts."
            ),
        ),
    ]


def _schema_seed(
    *,
    title: str,
    entity: str,
    schema: dict[str, str],
    body: str,
) -> SchemaSeedSpec:
    metadata = {
        "type": "schema",
        "entity": entity,
        "version": 1,
        "schema": schema,
        "settings": {"validation": "warn"},
    }
    return SchemaSeedSpec(
        title=title,
        entity=entity,
        content=f"# {title}\n\n{body}\n",
        metadata=metadata,
    )
