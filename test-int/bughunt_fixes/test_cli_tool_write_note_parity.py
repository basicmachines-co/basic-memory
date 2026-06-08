"""Bug hunt regression tests: `bm tool write-note` CLI/MCP parity.

Covers three confirmed bugs found by the integration-test bug hunt:

- #1 / #5: write-note exits 0 on a conflict/error JSON result (silent failure,
  inconsistent with delete-note/edit-note/search-notes which exit non-zero).
- #2: write-note had no `--overwrite` flag even though the MCP write_note tool
  supports overwrite=True to replace an existing note.

These are integration tests: real CliRunner -> CLI command -> MCP tool ->
in-process ASGI API -> real SQLite/Postgres DB and filesystem. No mocks.
"""

import asyncio
import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.tools import write_note as mcp_write_note

runner = CliRunner()


# --- #1: write-note exits non-zero on a conflict/error result ---


def _write_conflict(content_token: str):
    return runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Conflict Exit Note",
            "--folder",
            "parity-conflict",
            "--content",
            f"# Conflict Exit Note\n\n{content_token}",
            "--project",
            "test-project",
        ],
    )


def test_write_note_nonzero_exit_on_conflict_error(app, app_config, test_project, config_manager):
    """write-note should exit non-zero when the MCP result carries an error."""
    first = _write_conflict("FIRST")
    assert first.exit_code == 0, first.output

    second = _write_conflict("SECOND")
    payload = json.loads(second.stdout)

    # Confirm the MCP layer reported a conflict error in the JSON.
    assert payload.get("error") == "NOTE_ALREADY_EXISTS", payload
    assert payload.get("action") == "conflict", payload

    # Parity with delete-note / edit-note / search-notes: an error result
    # must drive a non-zero exit code so scripts can detect failure.
    assert second.exit_code != 0, (
        "write-note returned an error JSON payload "
        f"({payload.get('error')}) but exited 0. Sibling tool commands "
        "(delete-note, edit-note, search-notes) exit non-zero on error."
    )


# --- #5: blocked NOTE_ALREADY_EXISTS write must not report success ---


def _cli_write(project_name: str):
    return runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Conflict Note",
            "--folder",
            "conflict",
            "--content",
            "# Conflict Note\n\nFirst body.\n",
            "--project",
            project_name,
        ],
    )


def test_mcp_write_note_conflict_emits_error(app, app_config, test_project, config_manager):
    """Baseline: the MCP tool reports NOTE_ALREADY_EXISTS on a blocked re-write."""

    async def _go():
        first = await mcp_write_note(
            title="Conflict Note",
            content="# Conflict Note\n\nFirst body.\n",
            directory="conflict",
            project=test_project.name,
            output_format="json",
        )
        # output_format="json" returns a dict; narrow for the type checker.
        assert isinstance(first, dict)
        assert first.get("action") == "created"
        assert "error" not in first

        second = await mcp_write_note(
            title="Conflict Note",
            content="# Conflict Note\n\nSecond body (should be blocked).\n",
            directory="conflict",
            project=test_project.name,
            output_format="json",
        )
        return second

    second = asyncio.run(_go())
    assert isinstance(second, dict)
    assert second.get("error") == "NOTE_ALREADY_EXISTS"
    assert second.get("action") == "conflict"
    assert second.get("file_path") is None


def test_cli_write_note_conflict_should_exit_nonzero(app, app_config, test_project, config_manager):
    """CLI write-note must NOT exit 0 when the write was blocked by a conflict."""
    first = _cli_write(test_project.name)
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.stdout)
    assert first_payload["action"] == "created"

    second = _cli_write(test_project.name)

    assert "NOTE_ALREADY_EXISTS" in second.stdout, second.output

    assert second.exit_code != 0, (
        f"write-note exited {second.exit_code} after a blocked NOTE_ALREADY_EXISTS "
        "write; the note was NOT written but the CLI reported success"
    )


# --- #2: write-note --overwrite flag (MCP overwrite=True parity) ---


def _write_overwrite(args_extra: list[str]):
    return runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Overwrite Parity Note",
            "--folder",
            "parity-overwrite",
            "--content",
            "# Overwrite Parity Note\n\nVERSION_BODY",
            *args_extra,
        ],
    )


def test_write_note_cli_can_overwrite_like_mcp(app, app_config, test_project, config_manager):
    """CLI write-note must be able to overwrite an existing note (MCP overwrite=True)."""
    first = _write_overwrite(["--project", test_project.name])
    assert first.exit_code == 0, first.output
    first_data = json.loads(first.stdout)
    permalink = first_data["permalink"]

    second = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Overwrite Parity Note",
            "--folder",
            "parity-overwrite",
            "--content",
            "# Overwrite Parity Note\n\nNEW_VERSION_BODY",
            "--project",
            test_project.name,
            "--overwrite",
        ],
    )

    assert second.exit_code == 0, (
        "CLI write-note has no way to overwrite an existing note even though "
        "the MCP write_note tool supports overwrite=True. "
        f"exit_code={second.exit_code} output={second.output}"
    )

    read = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--project", test_project.name],
    )
    assert read.exit_code == 0, read.output
    read_data = json.loads(read.stdout)
    assert "NEW_VERSION_BODY" in (read_data.get("content") or "")
