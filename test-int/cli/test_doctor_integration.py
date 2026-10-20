"""`bm doctor` against a real app: reports, exit codes and the self-test flow (#1798)."""

import asyncio
import json
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import text, update
from typer.testing import CliRunner

from basic_memory import db
from basic_memory.cli.main import app as cli_app
from basic_memory.models import Project


def _invoke(*args: str):
    result = CliRunner().invoke(cli_app, ["doctor", *args], env={"COLUMNS": "240"})
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def test_doctor_json_reports_every_local_project(app, app_config, test_project, config_manager):
    result = _invoke("--skip-self-test", "--json")

    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert [project["name"] for project in output["projects"]] == [test_project.name]
    assert output["self_test"] == {"outcome": "skipped"}
    assert output["has_errors"] is False


@pytest.mark.asyncio
async def test_doctor_exits_nonzero_and_prints_the_fix_for_an_orphaned_row(
    app, app_config, test_project, config_manager, engine_factory
):
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        # A settled index: nothing is owed, so an orphaned row will not go away
        # on its own and doctor must call it an error.
        await session.execute(
            update(Project)
            .where(Project.id == test_project.id)
            .values(last_indexed_at=datetime.now(timezone.utc))
        )
        await session.execute(
            text(
                "INSERT INTO search_index (project_id, id, entity_id, type, title) "
                "VALUES (:project_id, 999, 999, 'entity', 'ghost')"
            ),
            {"project_id": test_project.id},
        )
        await session.commit()

    # The CLI runs its own event loop, so it cannot run on this test's loop.
    result = await asyncio.to_thread(_invoke, "--skip-self-test", "--json")

    assert result.exit_code == 1, result.output
    (project,) = json.loads(result.stdout)["projects"]
    (finding,) = project["findings"]
    assert finding["severity"] == "error"
    assert finding["command"] == f"bm reindex --search --project {test_project.name}"

    human = await asyncio.to_thread(_invoke, "--skip-self-test")
    assert human.exit_code == 1
    assert f"fix: bm reindex --search --project {test_project.name}" in human.stdout


def test_self_test_flow_writes_indexes_and_searches(
    app, app_config, test_project, config_manager, monkeypatch, tmp_path_factory
):
    """The child half of the sandboxed self-test, run in-process against the test app."""
    # The test project owns tmp_path, and projects cannot nest, so the self-test's
    # project directory goes in a sibling temp directory.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("doctor-sandbox")))

    result = _invoke("--self-test")

    assert result.exit_code == 0, result.output
    for step in (
        "OK API write created file",
        "OK project index processed manual file",
        "OK search confirmed manual file",
        "OK status observed indexed file",
    ):
        assert step in result.stdout
