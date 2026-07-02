"""Tests for CLI command utilities."""

import basic_memory.cloud.note_content_materialization as note_content_materialization
import basic_memory.db as db
import basic_memory.deps.services as deps_services
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
    monkeypatch.setattr(deps_services, "drain_background_tasks", fake_drain_background)
    monkeypatch.setattr(db, "shutdown_db", fake_shutdown)

    async def work() -> int:
        calls.append("work")
        return 42

    result = run_with_cleanup(work())

    assert result == 42
    assert calls == ["work", "drain-materializations", "drain-background", "shutdown"]
