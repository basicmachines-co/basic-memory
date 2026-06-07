"""Integration tests for `basic-memory tool delete-note`."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _write_note(
    title: str,
    folder: str,
    content: str,
    *,
    project: str | None = None,
) -> dict[str, Any]:
    args = [
        "tool",
        "write-note",
        "--title",
        title,
        "--folder",
        folder,
        "--content",
        content,
    ]
    if project is not None:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _read_note(identifier: str, *, project: str | None = None) -> dict[str, Any]:
    args = ["tool", "read-note", identifier]
    if project is not None:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _delete_note(
    identifier: str,
    *,
    is_directory: bool = False,
    project: str | None = None,
    project_id: str | None = None,
    local: bool = False,
) -> tuple[int, dict[str, Any], str]:
    args = ["tool", "delete-note", identifier]
    if is_directory:
        args.append("--is-directory")
    if project is not None:
        args.extend(["--project", project])
    if project_id is not None:
        args.extend(["--project-id", project_id])
    if local:
        args.append("--local")

    result = runner.invoke(cli_app, args)
    payload = json.loads(result.stdout) if result.stdout else {}
    return result.exit_code, payload, result.output


def _search_notes(
    query: str,
    *,
    mode_flag: str | None = None,
    page_size: int = 20,
) -> dict[str, Any]:
    args = ["tool", "search-notes", query, "--page-size", str(page_size)]
    if mode_flag is not None:
        args.append(mode_flag)

    result = runner.invoke(
        cli_app,
        args,
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _project_file(test_project, file_path: str) -> Path:
    return Path(test_project.path) / file_path


def test_delete_note_removes_file_database_record_and_search_result(
    app, app_config, test_project, config_manager
) -> None:
    """Single-note deletion removes the note from every user-visible surface."""
    note = _write_note(
        "CLI Delete Single Note",
        "delete-cli",
        "# CLI Delete Single Note\n\nUniqueSingleDeleteToken\n\n- [status] ready to delete",
    )
    note_path = _project_file(test_project, note["file_path"])
    assert note_path.exists()

    exit_code, payload, output = _delete_note(note["permalink"])

    assert exit_code == 0, output
    assert payload == {
        "deleted": True,
        "title": "CLI Delete Single Note",
        "permalink": note["permalink"],
        "file_path": note["file_path"],
    }
    assert not note_path.exists()

    missing = _read_note(note["permalink"])
    assert missing["title"] is None
    assert missing["permalink"] is None
    assert missing["content"] is None

    search = _search_notes("CLI Delete Single Note", mode_flag="--title")
    assert search["total"] == 0
    assert search["results"] == []


def test_delete_note_not_found_returns_json_without_error(
    app, app_config, test_project, config_manager
) -> None:
    """A missing note is machine-readable and does not produce a CLI failure."""
    exit_code, payload, output = _delete_note("delete-cli/missing-note")

    assert exit_code == 0, output
    assert payload == {
        "deleted": False,
        "title": None,
        "permalink": None,
        "file_path": None,
    }


def test_delete_note_case_mismatch_does_not_delete_exact_note(
    app, app_config, test_project, config_manager
) -> None:
    """Strict CLI deletes must not fuzzy-match a differently cased title."""
    note = _write_note(
        "CLI CamelCase Delete Note",
        "delete-cli",
        "# CLI CamelCase Delete Note\n\nCaseSensitiveDeleteToken",
    )

    exit_code, payload, output = _delete_note("cli camelcase delete note")

    assert exit_code == 0, output
    assert payload["deleted"] is False
    still_there = _read_note(note["permalink"])
    assert still_there["title"] == "CLI CamelCase Delete Note"
    assert "CaseSensitiveDeleteToken" in still_there["content"]


def test_delete_note_project_id_takes_precedence_over_wrong_project_name(
    app, app_config, test_project, config_manager
) -> None:
    """CLI `--project-id` routes destructive operations to the exact project."""
    note = _write_note(
        "CLI Delete By Project ID",
        "delete-cli",
        "# CLI Delete By Project ID\n\nProjectIdDeleteToken",
    )

    exit_code, payload, output = _delete_note(
        note["file_path"],
        project="not-the-test-project",
        project_id=test_project.external_id,
    )

    assert exit_code == 0, output
    assert payload["deleted"] is True
    assert payload["title"] == "CLI Delete By Project ID"
    assert _read_note(note["permalink"])["title"] is None


def test_delete_note_memory_url_detects_project_from_identifier(
    app, app_config, test_project, config_manager
) -> None:
    """A memory:// URL can select the project without a separate --project flag."""
    note = _write_note(
        "CLI Delete Memory URL",
        "delete-cli",
        "# CLI Delete Memory URL\n\nMemoryUrlDeleteToken",
        project=test_project.name,
    )
    memory_url = f"memory://{test_project.name}/{note['permalink']}"

    exit_code, payload, output = _delete_note(memory_url)

    assert exit_code == 0, output
    assert payload["deleted"] is True
    assert payload["permalink"] == note["permalink"]
    assert _read_note(note["permalink"], project=test_project.name)["title"] is None


def test_delete_directory_removes_nested_files_database_records_and_search_results(
    app, app_config, test_project, config_manager
) -> None:
    """Directory deletion removes nested notes and reports a complete JSON summary."""
    notes = [
        _write_note(
            "CLI Delete Directory Root",
            "delete-cli-dir",
            "# CLI Delete Directory Root\n\nDirectoryDeleteTokenRoot",
        ),
        _write_note(
            "CLI Delete Directory Child",
            "delete-cli-dir/child",
            "# CLI Delete Directory Child\n\nDirectoryDeleteTokenChild",
        ),
        _write_note(
            "CLI Delete Directory Deep Child",
            "delete-cli-dir/child/deep",
            "# CLI Delete Directory Deep Child\n\nDirectoryDeleteTokenDeep",
        ),
    ]
    note_paths = [_project_file(test_project, note["file_path"]) for note in notes]
    assert all(path.exists() for path in note_paths)

    exit_code, payload, output = _delete_note("delete-cli-dir", is_directory=True, local=True)

    assert exit_code == 0, output
    assert payload["deleted"] is True
    assert payload["is_directory"] is True
    assert payload["identifier"] == "delete-cli-dir"
    assert payload["total_files"] == 3
    assert payload["successful_deletes"] == 3
    assert payload["failed_deletes"] == 0
    assert payload["errors"] == []
    assert set(payload["deleted_files"]) == {note["file_path"] for note in notes}
    assert not any(path.exists() for path in note_paths)

    for note in notes:
        assert _read_note(note["permalink"])["title"] is None

    search = _search_notes("CLI Delete Directory", mode_flag="--title")
    assert search["total"] == 0
    assert search["results"] == []


def test_delete_directory_without_flag_does_not_delete_child_notes(
    app, app_config, test_project, config_manager
) -> None:
    """The CLI must not treat a directory path as destructive without --is-directory."""
    note = _write_note(
        "CLI Delete Directory Safety",
        "delete-cli-safety",
        "# CLI Delete Directory Safety\n\nDirectorySafetyToken",
    )

    exit_code, payload, output = _delete_note("delete-cli-safety")

    assert exit_code == 0, output
    assert payload["deleted"] is False
    still_there = _read_note(note["permalink"])
    assert still_there["title"] == "CLI Delete Directory Safety"
    assert _project_file(test_project, note["file_path"]).exists()


def test_delete_note_rejects_conflicting_routing_flags(
    app, app_config, test_project, config_manager
) -> None:
    """delete-note validates the same --local/--cloud conflict as other tool commands."""
    result = runner.invoke(
        cli_app,
        ["tool", "delete-note", "delete-cli/missing-note", "--local", "--cloud"],
    )

    assert result.exit_code != 0
    assert "Cannot specify both --local and --cloud" in result.output
