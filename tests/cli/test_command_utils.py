"""Tests for CLI command utilities."""

import basic_memory.cloud.note_content_materialization as note_content_materialization
import basic_memory.db as db
from basic_memory.cli.commands.command_utils import run_with_cleanup


def test_run_with_cleanup_drains_materializations_before_db_shutdown(monkeypatch):
    """One-shot clients must drain queued source-of-truth file writes before the DB
    is shut down and the event loop closes — otherwise the markdown write is lost."""
    calls: list[str] = []

    async def fake_drain() -> None:
        calls.append("drain")

    async def fake_shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(note_content_materialization, "drain_pending_materializations", fake_drain)
    monkeypatch.setattr(db, "shutdown_db", fake_shutdown)

    async def work() -> int:
        calls.append("work")
        return 42

    result = run_with_cleanup(work())

    assert result == 42
    assert calls == ["work", "drain", "shutdown"]
