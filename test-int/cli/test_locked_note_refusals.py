"""A refused overwrite must exit unsuccessfully and explain the note lock."""

import json
from pathlib import Path

from fastapi import FastAPI
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.models import Project


def test_overwrite_reports_lock_and_exits_nonzero(app: FastAPI, test_project: Project) -> None:
    runner = CliRunner()
    arguments = [
        "tool",
        "write-note",
        "--project",
        test_project.name,
        "--title",
        "Protected Runbook",
        "--folder",
        "protected",
    ]
    created = runner.invoke(
        cli_app, [*arguments, "--content", "---\nlocked: true\n---\n\nKeep this exact content.\n"]
    )
    assert created.exit_code == 0, created.output
    note = json.loads(created.stdout)
    path = Path(test_project.path) / note["file_path"]
    original = path.read_bytes()
    refused = runner.invoke(cli_app, [*arguments, "--content", "Replacement", "--overwrite"])
    assert refused.exit_code != 0
    # Debug logging may include the initial create conflict. Check the CLI's
    # actual failure message so logs cannot mask a misleading final result.
    errors = [
        line for line in refused.stderr.splitlines() if line.startswith("Error during write_note:")
    ]
    assert len(errors) == 1, refused.output
    assert "locked: true" in errors[0]
    assert "Note already exists" not in errors[0]
    assert path.read_bytes() == original
