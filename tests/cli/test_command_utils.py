"""Tests for CLI command utilities."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import basic_memory.cli.commands.command_utils as command_utils
import basic_memory.index.note_content_materialization as note_content_materialization
import basic_memory.db as db
import basic_memory.index.local_schedulers as local_schedulers
from basic_memory.cli.commands.command_utils import run_with_cleanup


def test_run_with_cleanup_drains_pending_work_before_db_shutdown(monkeypatch):
    """One-shot clients must drain queued source-of-truth file writes, then the
    background follow-up work those writes scheduled (vector sync, relation
    resolution), before the DB is shut down and the event loop closes — otherwise
    the markdown write is lost or semantic search is left stale."""
    calls: list[str] = []

    async def fake_drain_materializations() -> None:
        calls.append("drain-materializations")

    async def fake_drain_background() -> None:
        calls.append("drain-background")

    async def fake_shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(
        note_content_materialization,
        "drain_pending_materializations",
        fake_drain_materializations,
    )
    monkeypatch.setattr(local_schedulers, "drain_background_tasks", fake_drain_background)
    monkeypatch.setattr(db, "shutdown_db", fake_shutdown)

    async def work() -> int:
        calls.append("work")
        return 42

    result = run_with_cleanup(work())

    assert result == 42
    assert calls == ["work", "drain-materializations", "drain-background", "shutdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [
        ({"message": "Indexing started in background"}, "Indexing started in background"),
        (
            {"total_files": 3, "enqueued_files": 2, "enqueued_batches": 1, "deleted_files": 0},
            "Indexed 2/3 files (batches: 1, deleted orphans: 0)",
        ),
    ],
)
async def test_run_project_index_reports_background_and_foreground_responses(
    monkeypatch, response: dict[str, object], expected: str
):
    """`bm project add` no longer calls this; cloud project indexing still does."""
    printed: list[str] = []

    @asynccontextmanager
    async def fake_get_client(project_name: str | None = None) -> AsyncIterator[object]:
        yield object()

    class FakeProjectClient:
        def __init__(self, client: object) -> None:
            pass

        async def index(self, external_id: str, **kwargs: bool) -> dict[str, object]:
            assert external_id == "project-ext"
            return response

    monkeypatch.setattr(command_utils, "get_client", fake_get_client)
    monkeypatch.setattr(
        command_utils,
        "get_active_project",
        AsyncMock(return_value=SimpleNamespace(external_id="project-ext")),
    )
    monkeypatch.setattr(command_utils, "ProjectClient", FakeProjectClient)
    monkeypatch.setattr(
        command_utils.console, "print", lambda message="", *a, **k: printed.append(str(message))
    )

    await command_utils.run_project_index("research")

    [line] = printed
    assert expected in line.replace("[green]", "").replace("[/green]", "")
