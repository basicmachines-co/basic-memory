"""Tests for the `bm status --wait` compatibility behavior."""

import pytest

import basic_memory.cli.commands.status as status_module
from basic_memory.cli.commands.status import run_status
from basic_memory.schemas import ProjectIndexObservedFileResponse, ProjectIndexStatusResponse
from basic_memory.schemas.project_info import ProjectItem


@pytest.mark.asyncio
async def test_status_wait_returns_current_project_index_observation(monkeypatch, config_manager):
    """The event-index status path no longer exposes a pending-change counter."""
    project_item = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="scratch",
        path="/tmp/scratch",
        is_default=True,
    )
    project_index_status = ProjectIndexStatusResponse(
        total_files=1,
        observed_files=(
            ProjectIndexObservedFileResponse(
                path="notes/seed.md",
                checksum="abc123",
                size=12,
            ),
        ),
    )

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
        return project_item

    monkeypatch.setattr(status_module, "get_client", lambda **kwargs: FakeClientContext())
    monkeypatch.setattr(status_module, "get_active_project", fake_get_active_project)
    monkeypatch.setattr(status_module, "ProjectClient", FakeProjectClient)

    project_name, status = await run_status(
        project="scratch",
        wait=True,
        timeout=0.01,
        poll_interval=0.001,
    )

    assert project_name == "scratch"
    assert status.total_files == 1
    assert status.observed_files[0].path == "notes/seed.md"
