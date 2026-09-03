"""Tests for `bm status --wait`.

`--wait` was a documented no-op while the only signal was a pending count, which
reads zero both when work has drained and when none was ever queued. It waits on
the readiness phase now (#1414), so these pin both exits from the poll loop.
"""

import pytest

import basic_memory.cli.commands.status as status_module
from basic_memory.cli.commands.status import run_status
from basic_memory.schemas import ProjectIndexObservedFileResponse, ProjectIndexStatusResponse
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.schemas.project_readiness import ProjectIndexPhase
from tests.cli.conftest import make_readiness

PROJECT_ITEM = ProjectItem(
    id=1,
    external_id="11111111-1111-1111-1111-111111111111",
    name="scratch",
    path="/tmp/scratch",
    is_default=True,
)


def _install_fake_status(monkeypatch, project_index_status: ProjectIndexStatusResponse) -> None:
    """Route `run_status` at a canned response instead of the API."""

    class FakeProjectClient:
        def __init__(self, client):
            pass

        async def get_status(self, external_id):
            return project_index_status

    class FakeClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    async def fake_get_active_project(client, project, context):
        return PROJECT_ITEM

    monkeypatch.setattr(status_module, "get_client", lambda **kwargs: FakeClientContext())
    monkeypatch.setattr(status_module, "get_active_project", fake_get_active_project)
    monkeypatch.setattr(status_module, "ProjectClient", FakeProjectClient)


@pytest.mark.asyncio
async def test_status_wait_returns_as_soon_as_the_project_is_idle(monkeypatch, config_manager):
    """An already-settled project does not wait out the timeout."""
    project_index_status = ProjectIndexStatusResponse(
        total_files=1,
        observed_files=(
            ProjectIndexObservedFileResponse(
                path="notes/seed.md",
                checksum="abc123",
                size=12,
            ),
        ),
        readiness=make_readiness(files_on_disk=1, indexed_entities=1, files_total=1),
    )
    _install_fake_status(monkeypatch, project_index_status)

    project_name, status = await run_status(
        project="scratch",
        # A timeout this short would expire if the loop did not exit on IDLE.
        wait=True,
        timeout=30.0,
        poll_interval=10.0,
    )

    assert project_name == "scratch"
    assert status.total_files == 1
    assert status.observed_files[0].path == "notes/seed.md"


@pytest.mark.asyncio
async def test_status_wait_gives_up_on_a_never_indexed_project(monkeypatch, config_manager):
    """Waiting on a project nothing is indexing must end, and report the real state.

    Nothing will settle it until someone starts an index pass, so the loop has to
    return the honest phase rather than blocking forever.
    """
    project_index_status = ProjectIndexStatusResponse(
        total_files=2,
        observed_files=(),
        readiness=make_readiness(
            ProjectIndexPhase.NEVER_INDEXED,
            files_on_disk=2,
            files_pending=2,
            files_total=2,
        ),
    )
    _install_fake_status(monkeypatch, project_index_status)

    project_name, status = await run_status(
        project="scratch",
        wait=True,
        timeout=0.01,
        poll_interval=0.001,
    )

    assert project_name == "scratch"
    assert status.readiness.phase is ProjectIndexPhase.NEVER_INDEXED
