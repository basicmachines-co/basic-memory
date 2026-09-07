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
