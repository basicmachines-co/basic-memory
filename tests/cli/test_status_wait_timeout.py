"""Tests for the `bm status --wait` timeout guidance (#959)."""

import pytest

import basic_memory.cli.commands.status as status_module
from basic_memory.cli.commands.status import StatusTimeout, run_status
from basic_memory.schemas import SyncReportResponse
from basic_memory.schemas.project_info import ProjectItem


@pytest.mark.asyncio
async def test_status_wait_timeout_points_at_reindex(monkeypatch, config_manager):
    """The timeout error must hand the user the command that actually indexes.

    In CLI-only sessions no sync coordinator runs, so pending changes never
    drain and --wait always times out; without the hint the dead end looks
    like a hung indexer (#959).
    """
    project_item = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="scratch",
        path="/tmp/scratch",
        is_default=True,
    )
    pending_report = SyncReportResponse(new={"notes/seed.md"}, total=1)

    class FakeProjectClient:
        def __init__(self, client):
            pass

        async def get_status(self, external_id):
            return pending_report

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

    with pytest.raises(StatusTimeout) as exc_info:
        await run_status(project="scratch", wait=True, timeout=0.01, poll_interval=0.001)

    message = str(exc_info.value)
    assert "Timed out" in message
    assert "bm reindex --project scratch" in message
    assert "no Basic Memory server is running" in message
