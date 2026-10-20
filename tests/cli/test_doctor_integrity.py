"""Doctor must report existing anomalies and never claim unconditional success."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from rich.console import Console
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.doctor as doctor_cmd
from basic_memory.schemas.index_integrity import IndexIntegrityResponse
from basic_memory.schemas.project_info import ProjectItem, ProjectList


@pytest.fixture
def project_client():
    client = AsyncMock()
    client.list_projects.return_value = ProjectList(
        projects=[
            ProjectItem(id=1, external_id="one", name="First", path="/first"),
            ProjectItem(id=2, external_id="two", name="Second", path="/second"),
        ],
        default_project="First",
    )
    client.get_integrity.return_value = IndexIntegrityResponse(
        entities=1,
        search_rows=3,
        entity_rows=1,
        observation_rows=1,
        relation_rows=1,
        duplicate_keys=0,
        duplicate_rows=0,
        orphaned_rows=0,
        inverted_timestamps=0,
        examples=(),
    )
    return client


@pytest.mark.asyncio
async def test_checks_all_existing_projects(project_client, monkeypatch, capsys):
    monkeypatch.setattr(doctor_cmd, "console", Console(width=180))
    anomalies = await doctor_cmd._check_existing_projects(project_client, None)
    assert anomalies is False
    assert project_client.get_integrity.await_count == 2
    output = capsys.readouterr().out
    assert "First" in output and "Second" in output
    assert "3 search rows" in output and "checks passed" in output


@pytest.mark.asyncio
async def test_scopes_to_resolved_project(project_client):
    project_client.resolve_project.return_value.name = "Second"
    project_client.resolve_project.return_value.external_id = "two"
    await doctor_cmd._check_existing_projects(project_client, "Second")
    project_client.get_integrity.assert_awaited_once_with("two")
    project_client.list_projects.assert_not_awaited()


@pytest.mark.asyncio
async def test_reports_all_projects_before_warning(project_client, capsys):
    project_client.get_integrity.return_value = (
        project_client.get_integrity.return_value.model_copy(
            update={"orphaned_rows": 1, "examples": ("Orphan entity id=99",)}
        )
    )
    anomalies = await doctor_cmd._check_existing_projects(project_client, None)
    assert anomalies is True
    assert project_client.get_integrity.await_count == 2
    output = capsys.readouterr().out
    assert "Orphan entity id=99" in output
    assert "WARN" in output and "still run" in output


@pytest.mark.asyncio
async def test_unresolvable_project_is_an_error(project_client):
    project_client.resolve_project.side_effect = ValueError("Project 'Missing' not found")
    with pytest.raises(ValueError, match="not found"):
        await doctor_cmd._check_existing_projects(project_client, "Missing")


@pytest.mark.asyncio
async def test_api_failure_is_not_success(project_client):
    project_client.get_integrity.side_effect = ValueError("database unavailable")
    with pytest.raises(ValueError, match="database unavailable"):
        await doctor_cmd._check_existing_projects(project_client, None)


def test_project_option_reaches_doctor(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(doctor_cmd, "run_doctor", run)
    result = CliRunner().invoke(app, ["doctor", "--project", "Second"])
    assert result.exit_code == 0, result.output
    run.assert_awaited_once_with("Second")


def test_observed_anomalies_fail_doctor_after_smoke_test():
    doctor_cmd._fail_on_observed_anomalies(False)
    with pytest.raises(ValueError, match="integrity anomalies"):
        doctor_cmd._fail_on_observed_anomalies(True)


@pytest.mark.asyncio
async def test_anomaly_still_runs_disposable_smoke_test(monkeypatch):
    """Anomalies must not abort before doctor's file <-> database smoke test."""
    contexts = 0

    @asynccontextmanager
    async def client_context():
        nonlocal contexts
        contexts += 1
        if contexts == 2:
            raise RuntimeError("smoke test reached")
        yield object()

    async def preflight(_client, _project):
        return True

    monkeypatch.setattr(doctor_cmd, "get_client", client_context)
    monkeypatch.setattr(doctor_cmd, "_check_existing_projects", preflight)

    with pytest.raises(RuntimeError, match="smoke test reached"):
        await doctor_cmd.run_doctor()
    assert contexts == 2
