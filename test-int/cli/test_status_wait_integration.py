"""Integration test for `bm status --wait` against a real local project."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_status_wait_returns_once_indexed(app, app_config, test_project, config_manager):
    """status --wait exits 0 and returns the project-index observation."""
    # Write (and index) a note so the project has real content on disk + in DB.
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Wait Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Wait Test\n\nContent that should be indexed.",
        ],
    )
    assert write_result.exit_code == 0, write_result.output

    # --wait is a compatibility flag in the event-index flow and returns immediately.
    result = runner.invoke(cli_app, ["status", "--wait", "--json"])

    assert result.exit_code == 0, result.output
    start = result.output.index("{")
    data = json.loads(result.output[start:])
    assert data["total_files"] == 1
    assert data["observed_files"][0]["path"] == "test-notes/Wait Test Note.md"


def test_unindexed_status_counts_files_without_hashing(
    app, app_config, test_project, config_manager, monkeypatch
) -> None:
    """First status walks eligible paths without reading their contents."""
    from pathlib import Path

    from basic_memory.services import FileService

    root = Path(test_project.path)
    (root / "notes").mkdir()
    (root / "notes" / "new.md").write_text("# New\n", encoding="utf-8")
    (root / "asset.txt").write_text("unindexed asset", encoding="utf-8")
    (root / ".hidden.md").write_text("hidden", encoding="utf-8")

    async def unexpected_checksum(self: FileService, path: str) -> str:
        raise AssertionError(f"Unindexed status must not hash {path}")

    monkeypatch.setattr(FileService, "compute_checksum", unexpected_checksum)
    result = runner.invoke(cli_app, ["status", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["total_files"] == 2
    assert {item["path"] for item in data["observed_files"]} == {"notes/new.md", "asset.txt"}
    assert all(item["checksum"] is None for item in data["observed_files"])
    assert data["readiness"]["phase"] == "never_indexed"
    assert data["readiness"]["indexed_entities"] == 0
    files = next(stage for stage in data["readiness"]["stages"] if stage["name"] == "files")
    assert files["pending"] == files["total"] == 2
    assert {item["path"]: item["size"] for item in data["observed_files"]} == {
        "notes/new.md": (root / "notes" / "new.md").stat().st_size,
        "asset.txt": (root / "asset.txt").stat().st_size,
    }
