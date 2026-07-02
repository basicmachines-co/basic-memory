"""Tests for local note-content materialization adapters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.cloud.note_content_materialization as note_content_materialization
from basic_memory.cloud.note_content_materialization import (
    LocalNoteContentMaterializationProvider,
    LocalNoteContentStorage,
)
from basic_memory.indexing import FileIndexOperation, FileIndexResult
from basic_memory.runtime import (
    NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR,
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteMaterialization,
    runtime_note_content_payload_as_dict,
)
from basic_memory.services.file_service import FileService


class RecordingFileIndexer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def index_file(self, file_path: str, *, source: str) -> FileIndexResult:
        self.calls.append((file_path, source))
        return FileIndexResult(
            file_path=file_path,
            entity_id=42,
            external_id="note-1",
            title="Test note",
            permalink="notes/test",
            checksum="indexed-checksum",
            operation=FileIndexOperation.updated,
        )


def accepted_materialization_change() -> RuntimeAcceptedNoteChange[
    RuntimeNoteContentResponsePayload
]:
    return RuntimeAcceptedNoteChange(
        status_code=202,
        payload=RuntimeAcceptedNoteResponse(
            external_id="note-1",
            entity_id=42,
            title="Test note",
            note_type="note",
            content_type="text/markdown",
            permalink="notes/test",
            file_path="notes/test.md",
            markdown_content="# Test\n",
            entity_metadata={"topic": "runtime"},
            created_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
            updated_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
            created_by="creator",
            last_updated_by="editor",
            db_version=4,
            db_checksum="db-checksum",
            file_version=None,
            file_checksum=None,
            file_write_status="pending",
            last_source="api",
            file_updated_at=None,
            last_materialization_error=None,
        ),
        materialization=RuntimePendingNoteMaterialization(
            project_id=7,
            entity_id=42,
            db_version=4,
            db_checksum="db-checksum",
            source="api",
        ),
    )


def local_materialization_provider(
    indexer: RecordingFileIndexer,
    *,
    test_mode: bool = True,
) -> LocalNoteContentMaterializationProvider:
    # test_mode=True keeps materialization inline so these tests can assert the
    # result synchronously; production defers it to a background task.
    return LocalNoteContentMaterializationProvider(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_service=cast(FileService, object()),
        file_indexer=indexer,
        test_mode=test_mode,
    )


@pytest.mark.asyncio
async def test_local_note_content_storage_writes_accepted_markdown_bytes(tmp_path) -> None:
    """Accepted-note materialization stores the same bytes the DB snapshot checksums."""
    storage = LocalNoteContentStorage(FileService(tmp_path))
    content = "# Accepted\n\nUses LF bytes.\n"

    checksum = await storage.write_file("notes/accepted.md", content)

    assert (tmp_path / "notes" / "accepted.md").read_bytes() == content.encode("utf-8")
    assert checksum == sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_local_materialization_returns_conflict_without_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime file conflicts are returned to callers before local search indexing."""
    requests: list[RuntimeNoteMaterializationJobRequest] = []

    async def fake_run_note_materialization(
        request: RuntimeNoteMaterializationJobRequest,
        **_: Any,
    ) -> RuntimeNoteMaterializationResult:
        requests.append(request)
        return RuntimeNoteMaterializationResult(
            entity_id=42,
            status=RuntimeNoteMaterializationStatus.conflict,
            reason="Refusing to overwrite notes/test.md",
            file_path="notes/test.md",
            file_checksum="external-checksum",
        )

    monkeypatch.setattr(
        note_content_materialization,
        "run_note_materialization",
        fake_run_note_materialization,
    )
    indexer = RecordingFileIndexer()

    result = await local_materialization_provider(indexer).materialize_write_change(
        accepted_materialization_change()
    )

    assert requests == [
        RuntimeNoteMaterializationJobRequest(
            project_id=7,
            entity_id=42,
            db_version=4,
            db_checksum="db-checksum",
            source="api",
        )
    ]
    assert indexer.calls == []
    response_payload = runtime_note_content_payload_as_dict(result.payload)
    assert response_payload["file_write_status"] == "external_change_detected"
    assert response_payload["last_materialization_error"] == "Refusing to overwrite notes/test.md"
    assert response_payload["file_checksum"] == "external-checksum"
    assert response_payload["sync_error"] == NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR


@pytest.mark.asyncio
async def test_local_materialization_defers_write_off_the_accept_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production (test_mode=False) returns the accepted DB state at once, writes async.

    Cloud parity: the accept persists note_content and returns 202; the markdown
    file (source of truth) and its index are written off the request path.
    """
    requests: list[RuntimeNoteMaterializationJobRequest] = []

    async def fake_run_note_materialization(
        request: RuntimeNoteMaterializationJobRequest,
        **_: Any,
    ) -> RuntimeNoteMaterializationResult:
        requests.append(request)
        return RuntimeNoteMaterializationResult(
            entity_id=42,
            status=RuntimeNoteMaterializationStatus.conflict,
            reason="deferred",
            file_path="notes/test.md",
            file_checksum="c",
        )

    monkeypatch.setattr(
        note_content_materialization,
        "run_note_materialization",
        fake_run_note_materialization,
    )
    # Isolate the module-global pool so its workers don't outlive this test loop.
    pool = note_content_materialization._MaterializationWorkerPool()
    monkeypatch.setattr(note_content_materialization, "_materialization_pool", pool)
    indexer = RecordingFileIndexer()
    accepted = accepted_materialization_change()

    result = await local_materialization_provider(
        indexer, test_mode=False
    ).materialize_write_change(accepted)

    # Returned immediately with the accepted DB state — no inline write yet.
    assert result is accepted
    assert requests == []

    # The write happens off the accept path via the bounded pool; drain to confirm.
    await pool.join()
    assert len(requests) == 1
    await pool.aclose()


@pytest.mark.asyncio
async def test_drain_pending_materializations_waits_for_queued_work(monkeypatch) -> None:
    """One-shot clients must drain queued file writes before the loop closes, or the
    source-of-truth markdown file is lost even though the API reported it accepted."""
    pool = note_content_materialization._MaterializationWorkerPool()
    monkeypatch.setattr(note_content_materialization, "_materialization_pool", pool)
    ran = asyncio.Event()

    async def work() -> None:
        ran.set()

    pool.submit(work(), workers=1)
    await note_content_materialization.drain_pending_materializations()

    assert ran.is_set()
    await pool.aclose()


@pytest.mark.asyncio
async def test_local_materialization_schedules_relation_resolution_after_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the deferred index inserts the new note's rows, the materializer must
    back-resolve inbound forward references (#1002 review) — the eager router pass
    can run before the index lands."""
    accepted = accepted_materialization_change()

    async def fake_run_note_materialization(
        request: RuntimeNoteMaterializationJobRequest,
        **_: Any,
    ) -> RuntimeNoteMaterializationResult:
        return RuntimeNoteMaterializationResult(
            entity_id=42,
            status=RuntimeNoteMaterializationStatus.written,
            reason="written",
        )

    async def fake_load_indexed(**_: Any):
        return accepted.payload

    monkeypatch.setattr(
        note_content_materialization,
        "run_note_materialization",
        fake_run_note_materialization,
    )
    monkeypatch.setattr(
        note_content_materialization,
        "load_indexed_note_content_response_payload",
        fake_load_indexed,
    )

    scheduled: list[int] = []

    class RecordingScheduler:
        def schedule_relation_resolution(self, *, project_id: int) -> None:
            scheduled.append(project_id)

    provider = LocalNoteContentMaterializationProvider(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_service=cast(FileService, object()),
        file_indexer=RecordingFileIndexer(),
        test_mode=True,
        relation_resolution_scheduler=RecordingScheduler(),
    )

    await provider.materialize_write_change(accepted)

    assert accepted.materialization is not None
    assert scheduled == [accepted.materialization.project_id]


@pytest.mark.asyncio
async def test_materialization_pool_bounds_concurrency_and_drains() -> None:
    """Failsafe: the pool runs at most `workers` materializations at once.

    This bound is the whole point — unbounded create_task let every deferred
    write run concurrently and collapsed the tail under load.
    """
    pool = note_content_materialization._MaterializationWorkerPool()
    in_flight = 0
    peak = 0
    done = 0

    async def work() -> None:
        nonlocal in_flight, peak, done
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        done += 1

    for _ in range(20):
        pool.submit(work(), workers=3)
    await pool.join()

    assert done == 20  # every submitted materialization ran
    assert peak <= 3  # never more than `workers` in flight at once
    await pool.aclose()
