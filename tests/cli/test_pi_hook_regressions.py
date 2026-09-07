"""Regression coverage for Pi's shared hook boundary."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app

runner = CliRunner()


@pytest.mark.parametrize("settings", [None, {}, {"captureFolder": "pi/sessions"}])
def test_unmapped_pi_recall_never_queries_ambient_project(
    tmp_path: Path, settings: dict[str, str] | None
) -> None:
    if settings is not None:
        config = tmp_path / ".pi" / "basic-memory.json"
        config.parent.mkdir()
        config.write_text(json.dumps(settings), encoding="utf-8")
    with patch("basic_memory.mcp.tools.search_notes", new_callable=AsyncMock) as search:
        result = runner.invoke(
            app,
            ["hook", "session-start", "--harness", "pi", "--project-dir", str(tmp_path)],
            input=json.dumps({"session_id": "session-a", "cwd": str(tmp_path)}),
        )
    assert result.exit_code == 0
    assert "not configured" in result.stdout
    search.assert_not_awaited()


def test_pi_checkpoint_reuses_identity_and_separates_branches(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "basic-memory.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"project": "explicit-project"}), encoding="utf-8")
    write = AsyncMock(return_value={"action": "updated"})
    with patch("basic_memory.mcp.tools.write_note", write):
        for branch in ["branch-a", "branch-a", "branch-b"]:
            result = runner.invoke(
                app,
                ["hook", "pre-compact", "--harness", "pi", "--project-dir", str(tmp_path)],
                input=json.dumps(
                    {
                        "session_id": "session-a",
                        "branch_id": branch,
                        "cwd": str(tmp_path),
                        "turns": [{"role": "user", "text": "Preserve this decision"}],
                    }
                ),
            )
            assert result.exit_code == 0
            assert "Captured checkpoint:" in result.stdout
    calls = [call.kwargs for call in write.await_args_list]
    assert calls[0]["title"] == calls[1]["title"]
    assert calls[0]["title"] != calls[2]["title"]
    assert all(call["overwrite"] is True for call in calls)
    assert all(call["project"] == "explicit-project" for call in calls)


def test_pi_hook_brief_includes_checkpoint_excerpts(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "basic-memory.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"project": "explicit-project"}), encoding="utf-8")
    search = AsyncMock(
        side_effect=[
            {"results": []},
            {"results": []},
            {
                "results": [
                    {
                        "title": "Pi session hashed-title",
                        "permalink": "pi/sessions/pi-session-hashed-title",
                        "content": "Decision: keep bmCommand as an argv override.",
                    }
                ]
            },
        ]
    )
    with patch("basic_memory.mcp.tools.search_notes", search):
        result = runner.invoke(
            app,
            ["hook", "session-start", "--harness", "pi", "--project-dir", str(tmp_path)],
            input=json.dumps({"session_id": "session-a", "cwd": str(tmp_path)}),
        )

    assert result.exit_code == 0
    assert "Pi session hashed-title" in result.stdout
    assert "Decision: keep bmCommand as an argv override." in result.stdout


def test_pi_hook_settings_preserve_canonical_key_precedence(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "basic-memory.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "project": "explicit-project",
                "captureFolder": "canonical-folder",
                "capture_folder": "alias-folder",
                "recallTimeframe": "3d",
                "recall_timeframe": "30d",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["hook", "status", "--harness", "pi", "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "capture folder: canonical-folder" in result.stdout


def test_pi_checkpoint_includes_recent_assistant_turns(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "basic-memory.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"project": "explicit-project"}), encoding="utf-8")
    write = AsyncMock(return_value={"action": "updated"})
    with patch("basic_memory.mcp.tools.write_note", write):
        result = runner.invoke(
            app,
            ["hook", "pre-compact", "--harness", "pi", "--project-dir", str(tmp_path)],
            input=json.dumps(
                {
                    "session_id": "session-a",
                    "branch_id": "branch-a",
                    "cwd": str(tmp_path),
                    "turns": [
                        {"role": "user", "text": "Implement the installer"},
                        {"role": "assistant", "text": "Found the Windows path separator issue"},
                        {"role": "user", "text": "Preserve the early blocker"},
                        {"role": "assistant", "text": "x" * 220 + " durable suffix"},
                        {"role": "user", "text": "later user turn"},
                        {"role": "assistant", "text": "later assistant turn"},
                        {"role": "user", "text": "final user turn"},
                        {"role": "assistant", "text": "final assistant turn"},
                    ],
                }
            ),
        )

    assert result.exit_code == 0
    assert write.await_args is not None
    content = write.await_args.kwargs["content"]
    assert "**user:** Implement the installer" in content
    assert "**assistant:** Found the Windows path separator issue" in content
    assert "**user:** Preserve the early blocker" in content
    assert "durable suffix" in content


def test_pi_hook_settings_use_parent_workspace_config(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "basic-memory.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"project": "explicit-project"}), encoding="utf-8")
    child = tmp_path / "src" / "package"
    child.mkdir(parents=True)
    write = AsyncMock(return_value={"action": "updated"})
    with patch("basic_memory.mcp.tools.write_note", write):
        result = runner.invoke(
            app,
            ["hook", "pre-compact", "--harness", "pi", "--project-dir", str(child)],
            input=json.dumps(
                {
                    "session_id": "session-a",
                    "branch_id": "branch-a",
                    "cwd": str(child),
                    "turns": [{"role": "user", "text": "Preserve this decision"}],
                }
            ),
        )

    assert result.exit_code == 0
    assert write.await_args is not None
    assert write.await_args.kwargs["project"] == "explicit-project"


@pytest.mark.parametrize("branch", [None, "branch-a"])
def test_pi_failed_capture_never_reports_success(tmp_path: Path, branch: str | None) -> None:
    config = tmp_path / ".pi" / "basic-memory.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"project": "explicit-project"}), encoding="utf-8")
    write = AsyncMock(return_value={"error": "NOTE_WRITE_BLOCKED"})
    with patch("basic_memory.mcp.tools.write_note", write):
        result = runner.invoke(
            app,
            ["hook", "pre-compact", "--harness", "pi", "--project-dir", str(tmp_path)],
            input=json.dumps(
                {
                    "session_id": "session-a",
                    "branch_id": branch,
                    "turns": [{"role": "user", "text": "Preserve this decision"}],
                }
            ),
        )
    assert result.exit_code == 0  # Lifecycle errors remain fail-open for the host.
    assert "Captured checkpoint:" not in result.stdout
    assert result.stderr
    if branch is None:
        write.assert_not_awaited()
    else:
        assert "NOTE_WRITE_BLOCKED" in result.stderr
