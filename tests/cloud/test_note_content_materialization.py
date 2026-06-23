"""Tests for local note-content materialization adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.cloud.note_content_materialization as note_content_materialization
from basic_memory.cloud.note_content_materialization import LocalNoteContentMaterializationProvider
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
) -> LocalNoteContentMaterializationProvider:
    return LocalNoteContentMaterializationProvider(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_service=cast(FileService, object()),
        file_indexer=indexer,
    )


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
            tenant_id=note_content_materialization.LOCAL_NOTE_CONTENT_TENANT_ID,
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
