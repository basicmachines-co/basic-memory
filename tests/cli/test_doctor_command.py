"""Regression tests for doctor CLI failure output (#1027).

str() of a message-less exception (e.g. httpx.ReadTimeout, bare RuntimeError)
is empty, which used to leave users with a blank "Doctor failed:" line.
"""

from typing import Callable, NoReturn

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.doctor as doctor_cmd
from basic_memory.index import note_content_materialization

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


@pytest.mark.asyncio
async def test_doctor_waits_for_deferred_api_note_materialization(tmp_path, monkeypatch):
    api_file = tmp_path / "doctor" / "Doctor API Note.md"

    async def materialize_note() -> None:
        api_file.parent.mkdir(parents=True)
        api_file.write_text("# Doctor API Note", encoding="utf-8")

    monkeypatch.setattr(
        note_content_materialization,
        "drain_pending_materializations",
        materialize_note,
    )

    content = await doctor_cmd._read_materialized_api_note(
        api_file,
        "doctor/Doctor API Note.md",
    )

    assert content == "# Doctor API Note"


@pytest.mark.asyncio
async def test_doctor_reports_api_note_missing_after_materialization_drain(tmp_path, monkeypatch):
    async def drain_without_writing() -> None:
        pass

    monkeypatch.setattr(
        note_content_materialization,
        "drain_pending_materializations",
        drain_without_writing,
    )

    with pytest.raises(ValueError, match="API note file missing: doctor/missing.md"):
        await doctor_cmd._read_materialized_api_note(
            tmp_path / "doctor" / "missing.md",
            "doctor/missing.md",
        )


@pytest.mark.asyncio
async def test_doctor_cleanup_deletes_project_notes():
    """Doctor cleanup removes the disposable project's canonical files."""

    deleted: list[tuple[str, bool]] = []

    class ProjectClient:
        async def delete_project(
            self, project_external_id: str, delete_notes: bool = False
        ) -> None:
            deleted.append((project_external_id, delete_notes))

    await doctor_cmd._delete_doctor_project(ProjectClient(), "doctor-test", "project-id")

    assert deleted == [("project-id", True)]
