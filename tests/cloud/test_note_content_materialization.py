"""Tests for local note-content materialization adapters."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.cloud.note_content_materialization as note_content_materialization
from basic_memory.cloud.note_content_materialization import (
    InlineNoteFileDeleteEnqueuer,
    LocalNoteContentMaterializationProvider,
    LocalNoteContentStorage,
    recover_stuck_materializations,
    run_recovery_materialization,
)
from basic_memory import db
from basic_memory.models import Project
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.runtime.cleanup import RuntimeNoteFileDeleteJobRequest
from basic_memory.indexing.models import FileIndexOperation, FileIndexResult
from basic_memory.runtime.note_content import (
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
async def test_inline_delete_skips_old_path_aliasing_live_file(tmp_path) -> None:
    """A case-only rename must not delete the note's only file (P0 regression).

    On a case-insensitive filesystem the old and new paths are the same inode, so
    the checksum guard passes against the just-written new file and the cleanup
    would delete the note's only copy. A hard link reproduces that same-inode
    aliasing portably (case variants collide on the very filesystems where the
    hazard occurs, so distinct hard-linked names stand in for them).
    """
    content = b"# Note\n"
    (tmp_path / "notes").mkdir()
    live = tmp_path / "notes" / "foo.md"
    live.write_bytes(content)
    old_alias = tmp_path / "notes" / "alias.md"
    os.link(live, old_alias)  # same inode, as a case-only rename produces on APFS/NTFS
    checksum = sha256(content).hexdigest()

    enqueuer = InlineNoteFileDeleteEnqueuer(LocalNoteContentStorage(FileService(tmp_path)))
    await enqueuer.enqueue_note_file_delete(
        RuntimeNoteFileDeleteJobRequest(
            project_id=1,
            entity_id=7,
            file_path="notes/alias.md",
            file_checksum=checksum,
            live_file_path="notes/foo.md",
        )
    )

    assert live.exists(), "case-only rename deleted the note's only file"


@pytest.mark.asyncio
async def test_inline_delete_removes_genuine_old_file(tmp_path) -> None:
    """A real rename to a distinct path still cleans up the stale old file."""
    content = b"# Note\n"
    (tmp_path / "notes").mkdir()
    old = tmp_path / "notes" / "old.md"
    old.write_bytes(content)
    (tmp_path / "notes" / "new.md").write_bytes(content)
    checksum = sha256(content).hexdigest()

    enqueuer = InlineNoteFileDeleteEnqueuer(LocalNoteContentStorage(FileService(tmp_path)))
    await enqueuer.enqueue_note_file_delete(
        RuntimeNoteFileDeleteJobRequest(
            project_id=1,
            entity_id=7,
            file_path="notes/old.md",
            file_checksum=checksum,
            live_file_path="notes/new.md",
        )
    )

    assert not old.exists(), "genuine old file was not cleaned up after move"


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


# --- Startup recovery of stuck materializations ---


async def _seed_stuck_note_content(
    session_maker,
    *,
    project_id: int,
    entity_id: int,
    markdown_content: str,
    db_version: int,
    db_checksum: str,
    file_write_status: str,
) -> None:
    """Insert an accepted note_content row left mid-materialization by a crash."""
    repository = NoteContentRepository(project_id=project_id)
    async with db.scoped_session(session_maker) as session:
        await repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=entity_id,
                markdown_content=markdown_content,
                db_version=db_version,
                db_checksum=db_checksum,
                last_source="api",
                updated_at=datetime.now(UTC),
            ),
        )
        # accept_write always lands "pending"; a crash after the preflight would
        # have advanced it to "writing", so set the state we want to recover from.
        row = await repository.select_by_id(session, entity_id)
        assert row is not None
        row.file_write_status = file_write_status
        await session.flush()


@pytest.mark.asyncio
async def test_recover_stuck_materializations_writes_file_and_marks_synced(
    session_maker,
    test_project: Project,
    sample_entity,
    file_service: FileService,
) -> None:
    """A note stuck in 'writing' is re-materialized to disk and reaches 'synced'."""
    await _seed_stuck_note_content(
        session_maker,
        project_id=test_project.id,
        entity_id=sample_entity.id,
        markdown_content="# Recovered\n\nThe crash left this unwritten.\n",
        db_version=1,
        db_checksum="db-checksum-1",
        file_write_status="writing",
    )

    recovered = await recover_stuck_materializations(
        session_maker=session_maker,
        file_service=file_service,
        project_id=test_project.id,
    )

    assert recovered == 1
    written = file_service.base_path / sample_entity.file_path
    assert written.read_text(encoding="utf-8") == "# Recovered\n\nThe crash left this unwritten.\n"

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    assert row.file_write_status == "synced"
    assert row.file_checksum is not None
    assert row.file_version == 1


@pytest.mark.asyncio
async def test_recover_stuck_materializations_returns_zero_when_none_stuck(
    session_maker,
    test_project: Project,
    sample_entity,
    file_service: FileService,
) -> None:
    """A project with no writing/pending rows performs no recovery work."""
    await _seed_stuck_note_content(
        session_maker,
        project_id=test_project.id,
        entity_id=sample_entity.id,
        markdown_content="# Already materialized\n",
        db_version=1,
        db_checksum="db-checksum-1",
        file_write_status="synced",
    )

    recovered = await recover_stuck_materializations(
        session_maker=session_maker,
        file_service=file_service,
        project_id=test_project.id,
    )

    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_stuck_materializations_is_non_fatal_per_row(
    session_maker,
    test_project: Project,
    sample_entity,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that raises during recovery is logged and skipped, not propagated."""
    await _seed_stuck_note_content(
        session_maker,
        project_id=test_project.id,
        entity_id=sample_entity.id,
        markdown_content="# Poisoned\n",
        db_version=1,
        db_checksum="db-checksum-1",
        file_write_status="writing",
    )

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(note_content_materialization, "run_recovery_materialization", boom)

    recovered = await recover_stuck_materializations(
        session_maker=session_maker,
        file_service=file_service,
        project_id=test_project.id,
    )

    assert recovered == 0


@pytest.mark.asyncio
async def test_recover_stuck_materializations_does_not_overwrite_unexpected_file(
    session_maker,
    test_project: Project,
    sample_entity,
    file_service: FileService,
) -> None:
    """The write guard refuses to clobber a file it did not expect (not reverted)."""
    await _seed_stuck_note_content(
        session_maker,
        project_id=test_project.id,
        entity_id=sample_entity.id,
        markdown_content="# DB content\n",
        db_version=1,
        db_checksum="db-checksum-1",
        file_write_status="writing",
    )
    # A never-materialized row expects no file on disk (file_checksum is None);
    # an unexpected external file at the path trips the conflict guard.
    target = file_service.base_path / sample_entity.file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# External edit\n", encoding="utf-8")

    recovered = await recover_stuck_materializations(
        session_maker=session_maker,
        file_service=file_service,
        project_id=test_project.id,
    )

    assert recovered == 0
    assert target.read_text(encoding="utf-8") == "# External edit\n"

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    assert row.file_write_status == "external_change_detected"


@pytest.mark.asyncio
async def test_recover_stuck_materializations_publishes_already_written_file(
    session_maker,
    test_project: Project,
    sample_entity,
    file_service: FileService,
) -> None:
    """A crash after the file write but before publish is recovered, not a conflict.

    The row is left 'writing' with file_checksum None while the correct accepted
    content is already on disk. Recovery must recognise the same content, skip the
    redundant write, and publish to 'synced' instead of raising an external-change
    conflict — the arguably-more-common crash location the guard alone mishandles.
    """
    markdown_content = "# Recovered\n\nThe file was written before the crash.\n"
    await _seed_stuck_note_content(
        session_maker,
        project_id=test_project.id,
        entity_id=sample_entity.id,
        markdown_content=markdown_content,
        db_version=1,
        db_checksum="db-checksum-1",
        file_write_status="writing",
    )
    # Simulate the crash-after-write-before-publish window: the accepted content
    # is already on disk while the row still reads 'writing' (file_checksum None).
    target = file_service.base_path / sample_entity.file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    # write_bytes: the crash-written file must be byte-identical to the accepted
    # content; text mode would translate \n to \r\n on Windows and read as an
    # external edit instead of an already-written file
    target.write_bytes(markdown_content.encode("utf-8"))

    recovered = await recover_stuck_materializations(
        session_maker=session_maker,
        file_service=file_service,
        project_id=test_project.id,
    )

    assert recovered == 1
    assert target.read_text(encoding="utf-8") == markdown_content

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    assert row.file_write_status == "synced"
    assert row.file_checksum is not None
    assert row.file_version == 1


@pytest.mark.asyncio
async def test_run_recovery_materialization_does_not_revert_newer_accepted_version(
    session_maker,
    test_project: Project,
    sample_entity,
    file_service: FileService,
) -> None:
    """A stale recovery request (older db_version) must not overwrite the newer accepted note.

    Models the sweep capturing a stuck row at version N, then a concurrent accept
    advancing it to N+1 before recovery materializes: the db_version guard trips in
    preflight so the older content is never written.
    """
    await _seed_stuck_note_content(
        session_maker,
        project_id=test_project.id,
        entity_id=sample_entity.id,
        markdown_content="# Newer accepted v2\n",
        db_version=2,
        db_checksum="db-checksum-2",
        file_write_status="writing",
    )

    # Request built from the now-stale v1 snapshot the sweep would have captured.
    stale_request = RuntimeNoteMaterializationJobRequest(
        project_id=test_project.id,
        entity_id=sample_entity.id,
        db_version=1,
        db_checksum="db-checksum-1",
        source="note-content-materialization-recovery",
    )

    result = await run_recovery_materialization(
        stale_request,
        session_maker=session_maker,
        file_service=file_service,
    )

    assert result.status is RuntimeNoteMaterializationStatus.stale
    # The v1 content was never written; the accepted v2 row is intact.
    assert not (file_service.base_path / sample_entity.file_path).exists()
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    assert row.db_version == 2
    assert row.markdown_content == "# Newer accepted v2\n"
    assert row.file_write_status == "writing"
