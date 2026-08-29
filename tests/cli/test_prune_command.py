"""`bm prune` CLI wiring (#1254)."""

from types import SimpleNamespace

from typer.testing import CliRunner

from basic_memory.cli.app import app
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
