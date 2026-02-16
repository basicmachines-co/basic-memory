"""Integration tests for `basic-memory tool edit-note`."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _write_note(title: str, folder: str, content: str, project: str | None = None) -> dict:
    args = [
        "tool",
        "write-note",
        "--title",
        title,
        "--folder",
        folder,
        "--content",
        content,
        "--format",
        "json",
    ]
    if project:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _read_note(identifier: str, project: str | None = None) -> dict:
    args = ["tool", "read-note", identifier, "--format", "json"]
    if project:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_edit_note_append_success(app, app_config, test_project, config_manager):
    """append operation adds content to the end of the note."""
    note = _write_note(
        "Edit Append Note",
        "edit-tests",
        "# Append\n\nBASE_APPEND_MARKER",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "\nAPPENDED_MARKER",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert updated["content"].index("APPENDED_MARKER") > updated["content"].index(
        "BASE_APPEND_MARKER"
    )


def test_edit_note_prepend_success(app, app_config, test_project, config_manager):
    """prepend operation inserts content before existing body content."""
    note = _write_note(
        "Edit Prepend Note",
        "edit-tests",
        "# Prepend\n\nBASE_PREPEND_MARKER",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "prepend",
            "--content",
            "PREPENDED_MARKER\n",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert updated["content"].index("PREPENDED_MARKER") < updated["content"].index(
        "BASE_PREPEND_MARKER"
    )


def test_edit_note_find_replace_success_with_expected_count(
    app, app_config, test_project, config_manager
):
    """find_replace succeeds when expected replacement count matches actual count."""
    note = _write_note(
        "Edit Replace Note",
        "edit-tests",
        "# Replace\n\nFIND_ME_MARKER and FIND_ME_MARKER",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "find_replace",
            "--content",
            "REPLACED_MARKER",
            "--find-text",
            "FIND_ME_MARKER",
            "--expected-replacements",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert "FIND_ME_MARKER" not in updated["content"]
    assert updated["content"].count("REPLACED_MARKER") == 2


def test_edit_note_find_replace_fails_without_find_text(
    app, app_config, test_project, config_manager
):
    """find_replace requires --find-text."""
    note = _write_note(
        "Edit Missing Find Note",
        "edit-tests",
        "# Missing Find\n\nOriginal",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "find_replace",
            "--content",
            "Replacement",
        ],
    )

    assert result.exit_code != 0
    assert "find_text parameter is required for find_replace operation" in result.output


def test_edit_note_replace_section_success(app, app_config, test_project, config_manager):
    """replace_section updates exactly the targeted section body."""
    note = _write_note(
        "Edit Section Note",
        "edit-tests",
        "# Header\n\n## Keep\nKeep body\n\n## Target Section\nOld section body\n\n## After\nAfter body",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "replace_section",
            "--content",
            "New section body",
            "--section",
            "## Target Section",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert "New section body" in updated["content"]
    assert "Old section body" not in updated["content"]
    assert "## After" in updated["content"]


def test_edit_note_replace_section_fails_without_section(
    app, app_config, test_project, config_manager
):
    """replace_section requires --section."""
    note = _write_note(
        "Edit Missing Section Note",
        "edit-tests",
        "# Missing Section\n\nBody",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "replace_section",
            "--content",
            "Replacement body",
        ],
    )

    assert result.exit_code != 0
    assert "section parameter is required for replace_section operation" in result.output


def test_edit_note_json_format_contract(app, app_config, test_project, config_manager):
    """JSON format returns only metadata keys required by contract."""
    note = _write_note(
        "Edit JSON Note",
        "edit-tests",
        "# JSON\n\nBody",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "\nJSON_MARKER",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data.keys()) == {"title", "permalink", "file_path", "operation", "checksum"}
    assert data["operation"] == "append"
    assert data["title"] == "Edit JSON Note"


def test_edit_note_text_backend_failure_returns_nonzero(
    app, app_config, test_project, config_manager
):
    """Text mode should return non-zero when backend edit operation fails."""
    note = _write_note(
        "Edit Backend Failure Note",
        "edit-tests",
        "# Failure\n\nGamma",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "find_replace",
            "--find-text",
            "Gamma",
            "--content",
            "Delta",
            "--expected-replacements",
            "2",
        ],
    )

    assert result.exit_code != 0
    assert "# Edit Failed - Wrong Replacement Count" in result.output
    assert "Expected 2 occurrences of 'Gamma' but found 1" in result.output


def test_edit_note_project_and_routing_flag_parity(app, app_config, test_project, config_manager):
    """edit-note supports --project/--local and validates --local/--cloud conflict."""
    note = _write_note(
        "Edit Project Flag Note",
        "edit-tests",
        "# Project Flag\n\nPROJECT_FLAG_MARKER",
        project=test_project.name,
    )

    success = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "\nPROJECT_UPDATE_MARKER",
            "--project",
            test_project.name,
            "--local",
        ],
    )
    assert success.exit_code == 0, success.output
    assert "No such option" not in success.output

    updated = _read_note(note["permalink"], project=test_project.name)
    assert "PROJECT_UPDATE_MARKER" in updated["content"]

    conflict = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "ignored",
            "--local",
            "--cloud",
        ],
    )
    assert conflict.exit_code != 0
    assert "Cannot specify both --local and --cloud" in conflict.output
