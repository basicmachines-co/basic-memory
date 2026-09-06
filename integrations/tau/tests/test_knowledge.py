from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult
from pydantic import ValidationError
from tau.bridge import Settings
from tau.continuity import Note, SearchHit, SearchPage
from tau.knowledge import CodingProfile, CommandResult, coding_context, command
from test_recovery import boundary


def git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def test_profiles_are_explicit_scoped_and_validate(tmp_path: Path) -> None:
    profile = CodingProfile(
        root=tmp_path, repository="org/repo", project="private", read_projects=["team/shared"]
    )
    settings = Settings(project="general", repositories=[profile])
    assert settings.profile_for(tmp_path / "src") is profile
    assert settings.profile_for(tmp_path.parent).project == "general"
    assert settings.profile_for(None).project == "general"
    assert Settings(repositories=[profile]).profile_for(tmp_path).project == "private"
    with pytest.raises(ValidationError, match="unique"):
        Settings(repositories=[profile, profile])
    with pytest.raises(ValidationError, match="absolute"):
        CodingProfile(root=Path("relative"), repository="org/repo")
    for invalid in ({"read_projects": [""]}, {"project": " "}, {"auto_recall": "false"}):
        with pytest.raises(ValidationError):
            Settings.model_validate(invalid)


@pytest.mark.parametrize("pr_state", ["missing", "timeout", "none", "present", "invalid"])
async def test_git_metadata_and_optional_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pr_state: str
) -> None:
    from tau import knowledge

    git_repo(tmp_path)
    original = knowledge.command

    async def metadata_command(cwd: Path, *args: str) -> CommandResult:
        if args[0] != "gh":
            return await original(cwd, *args)
        if pr_state == "missing":
            raise FileNotFoundError
        if pr_state == "timeout":
            raise TimeoutError
        if pr_state == "none":
            return CommandResult(1, "")
        if pr_state == "invalid":
            return CommandResult(0, "{}")
        return CommandResult(
            0,
            json.dumps(
                {
                    "number": 42,
                    "title": "Shared memory",
                    "url": "https://example.invalid/42",
                    "state": "OPEN",
                    "baseRefName": "main",
                    "headRefName": "feature",
                }
            ),
        )

    monkeypatch.setattr(knowledge, "command", metadata_command)
    profile = CodingProfile(root=tmp_path, repository="org/repo", project="notes")
    if pr_state == "invalid":
        with pytest.raises(ValidationError):
            await coding_context(profile, tmp_path)
        return
    result = await coding_context(profile, tmp_path)
    assert result.branch == "main"
    assert len(result.git_sha) == 40
    assert result.metadata()["repository"] == "org/repo"
    if pr_state == "present":
        assert result.metadata()["pull_request_number"] == "42"
        assert result.metadata()["pull_request_state"] == "open"
    else:
        assert result.pull_request is None
        assert "pull_request_number" not in result.metadata()


async def test_coding_rejects_missing_git_and_nested_repo(tmp_path: Path) -> None:
    profile = CodingProfile(root=tmp_path, repository="org/parent")
    with pytest.raises(ValueError, match="initialized"):
        await coding_context(profile, tmp_path)
    git_repo(tmp_path / "nested")
    with pytest.raises(ValueError, match="differs"):
        await coding_context(profile, tmp_path / "nested")


async def test_metadata_command_cancellation_retires_process(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(
        command(
            tmp_path,
            sys.executable,
            "-c",
            f"import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)",
        )
    )
    async with asyncio.timeout(5):
        while not pidfile.exists():
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


async def test_recall_repository_identity_and_shared_sources(tmp_path: Path) -> None:
    git_repo(tmp_path)
    lifecycle, context, api = boundary()
    context.cwd = tmp_path
    lifecycle.settings = Settings(
        repositories=[
            CodingProfile(
                root=tmp_path,
                repository="org/repo",
                project="private",
                read_projects=["team/shared", "private", "team/shared"],
            )
        ]
    )
    queries: list[dict[str, object]] = []

    async def search(filters: dict[str, object], **kwargs: object) -> SearchPage:
        queries.append({"filters": filters, **kwargs})
        return SearchPage(results=[SearchHit(file_path="same-path.md")])

    lifecycle.search = AsyncMock(side_effect=search)
    lifecycle.read = AsyncMock(
        side_effect=lambda path, *, project=None: Note(file_path=path, content=f"From {project}")
    )
    lifecycle.connection.call = AsyncMock(
        return_value=CallToolResult(content=[], structured_content={"results": []})
    )
    await lifecycle.orient(context)
    assert lifecycle.last_error is None
    assert queries[0]["filters"] == {"repository": "org/repo"}
    assert queries[0]["note_types"] == ["coding_session"]
    assert sum(q["project"] == "team/shared" for q in queries) == 2
    inserted = api.append_message.call_args.args[0]
    assert "From private" in inserted and "From team/shared" in inserted
    assert "Read-only sources" in inserted
    assert not any(
        call.args[0] == "recent_activity" for call in lifecycle.connection.call.call_args_list
    )
    assert lifecycle.connection.call.call_args.args[1]["note_types"] == ["task", "decision"]
    api.append_entry.assert_not_called()


@pytest.mark.parametrize("coding", [False, True])
async def test_checkpoint_write_contract_and_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coding: bool
) -> None:
    from tau import knowledge

    lifecycle, context, api = boundary()
    context.cwd = tmp_path
    if coding:
        git_repo(tmp_path)
        lifecycle.settings = Settings(
            repositories=[
                CodingProfile(
                    root=tmp_path,
                    repository="org/repo",
                    project="private",
                    read_projects=["team/shared"],
                )
            ]
        )
        original = knowledge.command

        async def no_github(cwd: Path, *args: str) -> CommandResult:
            return CommandResult(1, "") if args[0] == "gh" else await original(cwd, *args)

        monkeypatch.setattr(knowledge, "command", no_github)
    else:
        lifecycle.settings = Settings(project="private", read_projects=["team/shared"])
    lifecycle.connection.call = AsyncMock(
        return_value=CallToolResult(
            content=[], structured_content={"file_path": "checkpoint.md", "action": "created"}
        )
    )
    await lifecycle.persist(
        context,
        kind="checkpoint",
        capture_id="new",
        source_tip="tip",
        content="- [summary] Work done",
        reason="test",
    )
    call = lifecycle.connection.call.call_args
    assert call.args[0] == "write_note"
    args = call.args[1]
    assert args["project"] == "private"
    assert args["note_type"] == ("coding_session" if coding else "session")
    metadata = args["metadata"]
    assert metadata["project"] == "private"
    assert metadata["started"] and metadata["ended"]
    assert metadata["tau_session_id"] == "s"
    assert metadata["capture"] == "summarized"
    assert ("repository" in metadata) is coding
    assert api.append_entry.call_args.args[1]["status"] == "confirmed"


def test_schema_bundles_match_canonical_sources() -> None:
    root = Path(__file__).resolve().parents[3]
    subprocess.run(
        [sys.executable, str(root / "scripts/sync_memory_schemas.py"), "--check"], check=True
    )
