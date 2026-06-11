"""Tests for `bm man install` (#952 / #610: make `man bm` work)."""

import subprocess

from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the module registers the man command group on the top-level app.
import basic_memory.cli.commands.man as man_command  # noqa: F401

runner = CliRunner()


def _flattened(output: str) -> str:
    # rich wraps console output at the terminal width, which differs between
    # local shells and CI — collapse all whitespace so phrase assertions can't
    # be split by a line break.
    return " ".join(output.split())


def test_man_install_writes_pages_to_target(tmp_path):
    """Install copies every bundled page into <root>/man1 as valid groff."""
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    bm_page = tmp_path / "man1" / "bm.1"
    alias_page = tmp_path / "man1" / "basic-memory.1"
    assert bm_page.exists()
    assert alias_page.exists()
    # groff sanity: a real title header and the alias .so include
    assert bm_page.read_text().startswith(".TH BM 1")
    assert alias_page.read_text().strip() == ".so man1/bm.1"
    assert "Try:" in _flattened(result.output)


def test_man_install_warns_when_root_not_on_manpath(tmp_path, monkeypatch):
    """A root provably absent from manpath output gets the MANPATH hint."""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["manpath"], returncode=0, stdout="/usr/share/man:/opt/man", stderr=""
        )

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "not on your manpath" in _flattened(result.output)
    assert "MANPATH" in _flattened(result.output)


def test_man_install_stays_quiet_when_manpath_unavailable(tmp_path, monkeypatch):
    """No manpath binary → no false-alarm warning, install still succeeds."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("manpath")

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "not on your manpath" not in _flattened(result.output)


def test_man_install_treats_manpath_failure_as_unknown(tmp_path, monkeypatch):
    """manpath exiting non-zero → unknown, no warning."""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=["manpath"], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "not on your manpath" not in _flattened(result.output)
