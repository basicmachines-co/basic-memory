"""`bm prune` CLI wiring (#1254)."""

from types import SimpleNamespace
from typing import cast

from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.config import BasicMemoryConfig
import basic_memory.cli.commands.prune as prune_cmd  # noqa: F401

runner = CliRunner()


def _configure(monkeypatch, captured: dict[str, object]):
    app_config = SimpleNamespace(default_project="main", database_backend="sqlite")
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", lambda: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_show_init_line", lambda *_args: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_show_cloud_promo", lambda *_args: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_run_periodic_auto_update", lambda *_args: None)
    monkeypatch.setattr(
        "basic_memory.cli.app.CliContainer.create",
        lambda: SimpleNamespace(config=app_config, mode=SimpleNamespace(is_cloud=False)),
    )
    monkeypatch.setattr("basic_memory.db.maybe_install_uvloop", lambda _config: None)
    monkeypatch.setattr(prune_cmd, "ConfigManager", lambda: SimpleNamespace(config=app_config))

    async def fake_prune(config, *, project, dry_run, yes):
        captured.update(project=project, dry_run=dry_run, yes=yes)

    monkeypatch.setattr(prune_cmd, "_prune", fake_prune)


def test_prune_passes_flags_through(monkeypatch):
    captured: dict[str, object] = {}
    _configure(monkeypatch, captured)

    result = runner.invoke(app, ["prune", "--project", "research", "--dry-run", "--yes"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"project": "research", "dry_run": True, "yes": True}


def test_prune_defaults_to_the_default_project_and_prompts(monkeypatch):
    captured: dict[str, object] = {}
    _configure(monkeypatch, captured)

    result = runner.invoke(app, ["prune"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"project": None, "dry_run": False, "yes": False}


def test_prune_requires_a_project_when_no_default_is_set():
    """An unset default_project must ask for --project, not report a cloud project 'None'."""
    import asyncio

    import typer

    app_config = cast(
        BasicMemoryConfig,
        SimpleNamespace(
            default_project=None,
            get_project_mode=lambda name: (_ for _ in ()).throw(AssertionError("mode checked")),
        ),
    )

    async def run():
        await prune_cmd._prune(app_config, project=None, dry_run=False, yes=False)

    try:
        asyncio.run(run())
    except typer.Exit as exit_info:
        assert exit_info.exit_code == 1
    else:  # pragma: no cover - the assertion above is the point of the test
        raise AssertionError("prune must exit when no project can be resolved")
