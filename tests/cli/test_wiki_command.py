"""CLI contract for local Wiki projection commands."""

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.wiki import WikiCommandReport, WikiProjectReport
from basic_memory.cli.main import app as cli_app
import basic_memory.cli.commands.wiki as wiki_command

runner = CliRunner()


def _configure_cli(monkeypatch, captured: list[dict[str, object]]) -> None:
    app_config = SimpleNamespace()
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", lambda: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_show_init_line", lambda *_args: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_show_cloud_promo", lambda *_args: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_run_periodic_auto_update", lambda *_args: None)
    monkeypatch.setattr(
        "basic_memory.cli.app.CliContainer.create",
        lambda: SimpleNamespace(config=app_config, mode=SimpleNamespace(is_cloud=False)),
    )
    monkeypatch.setattr("basic_memory.db.maybe_install_uvloop", lambda _config: None)
    monkeypatch.setattr(
        wiki_command,
        "ConfigManager",
        lambda: SimpleNamespace(config=app_config),
    )

    async def fake_execute(
        config,
        *,
        command,
        project_name,
        all_projects,
        dry_run,
    ):
        captured.append(
            {
                "config": config,
                "command": command,
                "project_name": project_name,
                "all_projects": all_projects,
                "dry_run": dry_run,
            }
        )
        return WikiCommandReport(
            command=command,
            dry_run=dry_run,
            success=True,
            projects=[
                WikiProjectReport(
                    project=project_name or "main",
                    path="/tmp/main",
                    state="current",
                    created=0,
                    updated=0,
                    unchanged=2,
                )
            ],
        )

    monkeypatch.setattr(wiki_command, "_execute_wiki_command", fake_execute)


def test_wiki_rebuild_aliases_have_identical_semantics(monkeypatch):
    captured: list[dict[str, object]] = []
    _configure_cli(monkeypatch, captured)

    for alias in ("rebuild", "init", "update"):
        result = runner.invoke(
            app,
            ["wiki", alias, "--project", "research", "--dry-run", "--json"],
        )
        assert result.exit_code == 0, result.stdout
        assert '"command":"rebuild"' in result.stdout

    assert [entry["command"] for entry in captured] == ["rebuild", "rebuild", "rebuild"]
    assert all(entry["project_name"] == "research" for entry in captured)
    assert all(entry["dry_run"] is True for entry in captured)


def test_wiki_validate_exits_nonzero_when_rebuild_is_needed(monkeypatch):
    captured: list[dict[str, object]] = []
    _configure_cli(monkeypatch, captured)

    async def fake_validate(*_args, **_kwargs):
        return WikiCommandReport(
            command="validate",
            success=False,
            projects=[
                WikiProjectReport(
                    project="main",
                    path="/tmp/main",
                    state="outdated",
                    created=0,
                    updated=1,
                    unchanged=1,
                    writes=["index.md"],
                )
            ],
        )

    monkeypatch.setattr(wiki_command, "_execute_wiki_command", fake_validate)

    result = runner.invoke(app, ["wiki", "validate", "--json"])

    assert result.exit_code == 1
    assert '"state":"outdated"' in result.stdout


def test_wiki_rejects_project_and_all_together(monkeypatch):
    captured: list[dict[str, object]] = []
    _configure_cli(monkeypatch, captured)

    result = runner.invoke(app, ["wiki", "status", "--project", "main", "--all"])

    assert result.exit_code == 1
    assert "either --project or --all" in result.stdout
    assert captured == []


def test_wiki_rebuild_builds_local_navigation(config_home, config_manager):
    note = config_home / "guides" / "start.md"
    note.parent.mkdir()
    note.write_text("---\ntitle: Start Here\n---\n\n# Start\n", encoding="utf-8")

    result = runner.invoke(
        cli_app,
        ["wiki", "rebuild", "--project", "test-project", "--json"],
        env={"BASIC_MEMORY_NO_PROMOS": "1"},
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["success"] is True
    assert report["projects"][0]["state"] == "current"
    assert (config_home / "index.md").is_file()
    assert (config_home / "guides" / "index.md").is_file()
    assert "[[guides/index|Guides]]" in (config_home / "index.md").read_text(encoding="utf-8")
