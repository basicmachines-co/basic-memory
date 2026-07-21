"""Structured coding-session context for harness checkpoints."""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands import hook as hook_module
from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _git_repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "feature"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "codex@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Codex"], cwd=repository, check=True)
    (repository / "README.md").write_text("# Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repository, check=True)
    (repository / ".codex").mkdir()
    (repository / ".claude").mkdir()
    return repository


def _write_config(repo_path: Path, **overrides: object) -> None:
    config: dict[str, object] = {
        "primaryProject": "demo",
        "sessionProfile": "coding",
        "repository": "basicmachines-co/basic-memory",
        **overrides,
    }
    (repo_path / ".codex" / "basic-memory.json").write_text(
        json.dumps({"basicMemory": config}), encoding="utf-8"
    )


def _write_claude_config(repo_path: Path, **overrides: object) -> None:
    config: dict[str, object] = {
        "primaryProject": "demo",
        "sessionProfile": "coding",
        "repository": "basicmachines-co/basic-memory",
        **overrides,
    }
    (repo_path / ".claude" / "settings.json").write_text(
        json.dumps({"basicMemory": config}), encoding="utf-8"
    )


def _transcript(tmp_path: Path) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps(
            {
                "message": {"role": "user", "content": "Add queryable coding sessions"},
                "type": "user",
            }
        ),
        encoding="utf-8",
    )
    return path


def _payload(repository: Path, transcript: Path) -> str:
    return json.dumps(
        {
            "session_id": "session-1",
            "cwd": str(repository),
            "transcript_path": str(transcript),
            "trigger": "auto",
        }
    )


def test_coding_profile_writes_required_git_and_pull_request_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "bm-home"))
    repository = _git_repo(tmp_path)
    _write_config(repository)
    transcript = _transcript(tmp_path)
    pull_request = hook_module.PullRequestContext(
        number=1124,
        title="feat(plugins): add coding sessions",
        url="https://github.com/basicmachines-co/basic-memory/pull/1124",
        state="open",
        base_branch="main",
        head_branch="feature",
    )
    mock_write = AsyncMock(return_value={"action": "created"})
    with (
        patch("basic_memory.mcp.tools.write_note", mock_write),
        patch.object(hook_module, "_pull_request_context", return_value=pull_request),
    ):
        result = runner.invoke(
            cli_app,
            ["hook", "pre-compact", "--harness", "codex", "--project-dir", str(repository)],
            input=_payload(repository, transcript),
        )

    assert result.exit_code == 0
    assert mock_write.await_args is not None
    kwargs = mock_write.await_args.kwargs
    metadata = kwargs["metadata"]
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert kwargs["note_type"] == "coding_session"
    assert metadata["project"] == "demo"
    assert metadata["repository"] == "basicmachines-co/basic-memory"
    assert metadata["repo_root"]
    assert metadata["cwd"]
    assert metadata["repo_root"] == metadata["cwd"]
    assert metadata["branch"] == "feature"
    assert metadata["git_sha"] == expected_sha
    assert metadata["pull_request_number"] == "1124"
    assert metadata["pull_request_state"] == "open"
    assert metadata["pull_request_base"] == "main"
    assert metadata["pull_request_head"] == "feature"
    assert "## Repository" in kwargs["content"]
    assert "Pull request: #1124" in kwargs["content"]


def test_coding_profile_omits_pull_request_fields_when_branch_has_no_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "bm-home"))
    repository = _git_repo(tmp_path)
    _write_config(repository)
    transcript = _transcript(tmp_path)
    mock_write = AsyncMock(return_value={"action": "created"})
    with (
        patch("basic_memory.mcp.tools.write_note", mock_write),
        patch.object(hook_module, "_pull_request_context", return_value=None),
    ):
        result = runner.invoke(
            cli_app,
            ["hook", "pre-compact", "--harness", "codex", "--project-dir", str(repository)],
            input=_payload(repository, transcript),
        )

    assert result.exit_code == 0
    assert mock_write.await_args is not None
    metadata = mock_write.await_args.kwargs["metadata"]
    assert metadata["repository"] == "basicmachines-co/basic-memory"
    assert "pull_request_number" not in metadata


def test_claude_coding_profile_writes_coding_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "bm-home"))
    repository = _git_repo(tmp_path)
    _write_claude_config(repository)
    transcript = _transcript(tmp_path)
    mock_write = AsyncMock(return_value={"action": "created"})
    with (
        patch("basic_memory.mcp.tools.write_note", mock_write),
        patch.object(hook_module, "_pull_request_context", return_value=None),
    ):
        result = runner.invoke(
            cli_app,
            ["hook", "pre-compact", "--harness", "claude", "--project-dir", str(repository)],
            input=_payload(repository, transcript),
        )

    assert result.exit_code == 0
    assert mock_write.await_args is not None
    kwargs = mock_write.await_args.kwargs
    assert kwargs["note_type"] == "coding_session"
    assert kwargs["metadata"]["repository"] == "basicmachines-co/basic-memory"
    assert kwargs["metadata"]["claude_session_id"] == "session-1"


def test_coding_profile_requires_confirmed_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "bm-home"))
    repository = _git_repo(tmp_path)
    _write_config(repository, repository="")
    transcript = _transcript(tmp_path)
    mock_write = AsyncMock()
    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = runner.invoke(
            cli_app,
            ["hook", "pre-compact", "--harness", "codex", "--project-dir", str(repository)],
            input=_payload(repository, transcript),
        )

    assert result.exit_code == 0
    assert "coding session profile requires basicMemory.repository" in result.stderr
    mock_write.assert_not_awaited()


def test_coding_profile_uses_dedicated_schema_for_both_harnesses() -> None:
    codex = hook_module.PROFILES[hook_module.Harness.codex]
    claude = hook_module.PROFILES[hook_module.Harness.claude]

    assert codex.coding_session_note_type == "coding_session"
    assert claude.coding_session_note_type == "coding_session"


def test_coding_recall_filters_by_repository_and_merges_legacy_sessions() -> None:
    queries: list[dict[str, object]] = []

    async def fake_query(project: str | None, **filters: object) -> dict:
        queries.append({"project": project, **filters})
        if filters.get("note_types") == ["coding_session"]:
            return {"results": [{"title": "Coding", "permalink": "sessions/coding"}]}
        if filters.get("note_types") == ["codex_session", "session"]:
            return {
                "results": [
                    {"title": "Duplicate", "permalink": "sessions/coding"},
                    {"title": "Legacy", "permalink": "sessions/legacy"},
                ]
            }
        return {"results": []}

    profile = hook_module.PROFILES[hook_module.Harness.codex]
    with patch.object(hook_module, "_query", side_effect=fake_query):
        context = asyncio.run(
            hook_module._gather_context(
                profile,
                "demo",
                "7d",
                [],
                repository="basicmachines-co/basic-memory",
            )
        )

    coding_query = next(query for query in queries if query.get("note_types") == ["coding_session"])
    assert coding_query["metadata_filters"] == {"repository": "basicmachines-co/basic-memory"}
    assert [row["title"] for row in hook_module._rows(context.sessions)] == ["Coding", "Legacy"]


def test_coding_recall_requires_configured_repository() -> None:
    profile = hook_module.PROFILES[hook_module.Harness.codex]
    with patch.object(hook_module, "_gather_context") as gather_context:
        brief = hook_module._build_brief(
            profile,
            {"primaryProject": "demo", "sessionProfile": "coding"},
            configured=True,
        )

    assert "Coding session setup is incomplete" in brief
    assert "basicMemory.repository" in brief
    gather_context.assert_not_called()


def test_required_git_value_rejects_non_repository(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="could not read Git context"):
        hook_module._required_git_value(str(tmp_path), "rev-parse", "HEAD")


def test_required_git_value_wraps_process_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no git")),
    )

    with pytest.raises(RuntimeError, match="could not read Git context"):
        hook_module._required_git_value("/tmp/repo", "rev-parse", "HEAD")


def test_pull_request_context_is_optional_without_usable_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook_module.shutil, "which", lambda name: None)
    assert hook_module._pull_request_context("/tmp/repo") is None

    monkeypatch.setattr(hook_module.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=1),
    )
    assert hook_module._pull_request_context("/tmp/repo") is None

    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no exec")),
    )
    assert hook_module._pull_request_context("/tmp/repo") is None


@pytest.mark.parametrize("payload", ["not-json", "{}"])
def test_pull_request_context_rejects_invalid_payload(
    payload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook_module.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout=payload),
    )

    assert hook_module._pull_request_context("/tmp/repo") is None


def test_pull_request_context_parses_gh_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        hook_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "number": 42,
                    "title": "Ship it",
                    "url": "https://github.com/owner/repo/pull/42",
                    "state": "MERGED",
                    "baseRefName": "main",
                    "headRefName": "feature",
                }
            ),
        ),
    )

    assert hook_module._pull_request_context("/tmp/repo") == hook_module.PullRequestContext(
        number=42,
        title="Ship it",
        url="https://github.com/owner/repo/pull/42",
        state="merged",
        base_branch="main",
        head_branch="feature",
    )
