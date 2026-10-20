"""`bm doctor` against a real app: reports, exit codes and the self-test flow (#1798)."""

import asyncio
import json
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import text, update
from typer.testing import CliRunner

from basic_memory import db
import basic_memory.cli.commands.doctor as doctor_cmd
from basic_memory.cli.main import app as cli_app
from basic_memory.config_models import ProjectEntry, ProjectMode, resolve_data_dir
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository


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
    assert finding["command"] == f"bm reindex --search --full --project {test_project.name}"

    human = await asyncio.to_thread(_invoke, "--skip-self-test")
    assert human.exit_code == 1
    assert f"fix: bm reindex --search --full --project {test_project.name}" in human.stdout


@pytest.mark.asyncio
async def test_cloud_mode_projects_are_skipped_not_reported(
    app, app_config, test_project, config_manager, engine_factory, tmp_path_factory
):
    """Local remedies cannot fix a cloud project, so doctor must never report one."""
    cloud_path = tmp_path_factory.mktemp("cloud-copy")
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        await ProjectRepository().create(
            session,
            {"name": "research", "path": str(cloud_path), "is_active": True, "is_default": False},
        )
    config = config_manager.load_config()
    config.projects["research"] = ProjectEntry(path=str(cloud_path), mode=ProjectMode.CLOUD)
    config_manager.save_config(config)

    result = await asyncio.to_thread(_invoke, "--skip-self-test", "--json")

    output = json.loads(result.stdout)
    assert output["skipped_cloud_projects"] == ["research"]
    assert [project["name"] for project in output["projects"]] == [test_project.name]


def test_config_only_cloud_projects_are_skipped_without_a_local_lookup(
    app, app_config, test_project, config_manager, tmp_path_factory
):
    """`add --cloud` and `set-cloud` leave no local database row to resolve."""
    config = config_manager.load_config()
    config.projects["remote"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("remote")), mode=ProjectMode.CLOUD
    )
    config_manager.save_config(config)

    every_project = _invoke("--skip-self-test", "--json")
    named = _invoke("--project", "remote", "--skip-self-test", "--json")

    assert every_project.exit_code == 0, every_project.output
    assert json.loads(every_project.stdout)["skipped_cloud_projects"] == ["remote"]
    assert named.exit_code == 0, named.output
    named_output = json.loads(named.stdout)
    assert named_output["projects"] == []
    assert named_output["skipped_cloud_projects"] == ["remote"]


def test_self_test_flow_writes_indexes_and_searches(
    app, app_config, test_project, config_manager, monkeypatch, tmp_path_factory
):
    """The child half of the sandboxed self-test, run in-process against the test app."""
    # The test project owns tmp_path, and projects cannot nest, so the self-test's
    # project directory goes in a sibling temp directory.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("doctor-sandbox")))
    # The test config directory stands in for the sandbox the parent would create.
    monkeypatch.setenv(doctor_cmd.SANDBOX_MARKER, str(resolve_data_dir()))

    result = _invoke("--self-test")

    assert result.exit_code == 0, result.output
    for step in (
        "OK API write created file",
        "OK project index processed manual file",
        "OK search confirmed manual file",
        "OK status observed indexed file",
    ):
        assert step in result.stdout


@pytest.mark.parametrize("marker", [None, "elsewhere"])
def test_self_test_refuses_to_run_outside_its_sandbox(
    app, app_config, test_project, config_manager, monkeypatch, tmp_path_factory, marker
):
    """Run by hand, --self-test would leave a project in the user's real config."""
    if marker is None:
        monkeypatch.delenv(doctor_cmd.SANDBOX_MARKER, raising=False)
    else:
        monkeypatch.setenv(doctor_cmd.SANDBOX_MARKER, str(tmp_path_factory.mktemp(marker)))
    projects_before = set(config_manager.load_config().projects)

    result = _invoke("--self-test")

    assert result.exit_code == 1
    assert "only runs inside the sandbox" in " ".join(result.output.split())
    assert set(config_manager.load_config().projects) == projects_before
