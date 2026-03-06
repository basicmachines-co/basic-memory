"""Tests for `bm update` command."""

from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.auto_update import AutoUpdateResult, AutoUpdateStatus, InstallSource


def _result(
    status: AutoUpdateStatus,
    *,
    message: str | None,
    error: str | None = None,
) -> AutoUpdateResult:
    return AutoUpdateResult(
        status=status,
        source=InstallSource.UV_TOOL,
        checked=True,
        update_available=status in {AutoUpdateStatus.UPDATE_AVAILABLE, AutoUpdateStatus.UPDATED},
        updated=status == AutoUpdateStatus.UPDATED,
        latest_version="9.9.9",
        message=message,
        error=error,
        restart_recommended=status == AutoUpdateStatus.UPDATED,
    )


def test_update_command_applies_upgrade(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(
        "basic_memory.cli.commands.update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.UPDATED,
            message="Basic Memory was updated successfully.",
        ),
    )

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "updated successfully" in result.stdout.lower()


def test_update_command_check_only_shows_available(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(
        "basic_memory.cli.commands.update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.UPDATE_AVAILABLE,
            message="Update available (latest: 9.9.9). Run `uv tool upgrade basic-memory`.",
        ),
    )

    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "update available" in result.stdout.lower()


def test_update_command_reports_up_to_date(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(
        "basic_memory.cli.commands.update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.UP_TO_DATE,
            message="Basic Memory is up to date.",
        ),
    )

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "up to date" in result.stdout.lower()


def test_update_command_failure_exits_nonzero(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(
        "basic_memory.cli.commands.update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.FAILED,
            message="Automatic update failed.",
            error="network timeout",
        ),
    )

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "automatic update failed" in result.stdout.lower()
