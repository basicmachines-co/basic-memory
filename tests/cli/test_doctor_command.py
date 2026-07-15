"""Regression tests for doctor CLI failure output (#1027).

str() of a message-less exception (e.g. httpx.ReadTimeout, bare RuntimeError)
is empty, which used to leave users with a blank "Doctor failed:" line.
"""

from typing import Callable, NoReturn

from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.doctor as doctor_cmd

runner = CliRunner()


def _raise(exc: Exception) -> Callable[[], NoReturn]:
    def raiser() -> NoReturn:
        raise exc

    return raiser


def test_doctor_failure_prints_error_message(monkeypatch):
    """Exceptions with a message keep printing that message."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(ValueError("doctor project missing")))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Doctor failed: doctor project missing" in result.output


def test_doctor_failure_message_never_blank(monkeypatch):
    """A message-less expected error falls back to the repr instead of blank output."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(ValueError()))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Doctor failed: ValueError()" in result.output


def test_doctor_unexpected_failure_message_never_blank(monkeypatch):
    """A message-less unexpected error (generic handler) also shows its repr on stderr."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(RuntimeError()))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Doctor failed: RuntimeError()" in result.stderr
