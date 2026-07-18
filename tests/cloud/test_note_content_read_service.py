from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.services.note_content_reads as note_content_reads
from basic_memory.services.note_content_reads import NoteContentQueryService
from basic_memory.runtime.note_content import (
    NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR,
    runtime_note_content_payload_as_dict,
)


def _entity(**overrides: object) -> SimpleNamespace:
    now = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "external_id": str(uuid4()),
        "id": 42,
        "title": "Test note",
        "note_type": "note",
        "content_type": "text/markdown",
        "permalink": "main/notes/test-note",
        "file_path": "notes/test-note.md",
        "content": None,
        "entity_metadata": {"status": "draft"},
        "observations": [],
        "relations": [],
        "created_at": now,
        "updated_at": now,
        "created_by": "creator",
        "last_updated_by": "editor",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _note_content(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "markdown_content": "# Test note\n",
        "db_version": 4,
        "db_checksum": "db-checksum",
        "file_version": 3,
        "file_checksum": "file-checksum",
        "file_write_status": "synced",
        "last_source": "api",
        "last_materialization_error": None,
        "file_updated_at": datetime(2026, 4, 13, 13, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _view(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "entity": _entity(),
        "note_content": _note_content(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _query_service() -> NoteContentQueryService:
    return NoteContentQueryService(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )


@pytest.mark.asyncio
async def test_note_content_query_service_shapes_note_content_sync_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot entity reads expose accepted DB content plus materialization state."""
    note_view = _view()
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_query_view",
        AsyncMock(return_value=note_view),
    )

    payload = await _query_service().get_note_entity_payload(
        project_external_id="main",
        entity_external_id=str(uuid4()),
    )

    assert payload is not None
    response_payload = runtime_note_content_payload_as_dict(payload)
    assert response_payload["external_id"] == note_view.entity.external_id
    assert response_payload["content"] == "# Test note\n"
    assert response_payload["db_version"] == 4
    assert response_payload["db_checksum"] == "db-checksum"
    assert response_payload["file_version"] == 3
    assert response_payload["file_checksum"] == "file-checksum"
    assert response_payload["file_write_status"] == "synced"
    assert response_payload["last_source"] == "api"
    assert (
        response_payload["file_updated_at"] == datetime(2026, 4, 13, 13, 0, tzinfo=UTC).isoformat()
    )
    assert "sync_error" not in response_payload


@pytest.mark.asyncio
async def test_note_content_query_service_adds_recovery_hint_for_external_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conflict reads carry one clear recovery hint for clients."""
    note_view = _view(
        note_content=_note_content(
            file_write_status="external_change_detected",
            last_materialization_error="Refusing to overwrite unexpected file",
        )
    )
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_query_view",
        AsyncMock(return_value=note_view),
    )

    payload = await _query_service().get_note_entity_payload(
        project_external_id="main",
        entity_external_id=str(uuid4()),
    )

    assert payload is not None
    response_payload = runtime_note_content_payload_as_dict(payload)
    assert response_payload["file_write_status"] == "external_change_detected"
    assert response_payload["last_materialization_error"] == (
        "Refusing to overwrite unexpected file"
    )
    assert response_payload["sync_error"] == NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR


@pytest.mark.asyncio
async def test_note_content_query_service_returns_file_metadata_without_note_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary files remain first-class entities without markdown note_content."""
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_query_view",
        AsyncMock(
            return_value=_view(
                entity=_entity(
                    external_id="note-456",
                    title="diagram.png",
                    note_type="file",
                    content_type="image/png",
                    permalink="Main/diagram.png",
                    file_path="diagram.png",
                    entity_metadata={"type": "file"},
                ),
                note_content=None,
            )
        ),
    )

    payload = await _query_service().get_note_entity_payload(
        project_external_id="project-123",
        entity_external_id="note-456",
    )

    assert payload is not None
    response_payload = runtime_note_content_payload_as_dict(payload)
    assert response_payload["external_id"] == "note-456"
    assert response_payload["title"] == "diagram.png"
    assert response_payload["note_type"] == "file"
    assert response_payload["content_type"] == "image/png"
    assert response_payload["file_path"] == "diagram.png"
    assert response_payload["content"] is None
    assert "db_version" not in response_payload


@pytest.mark.asyncio
async def test_note_content_query_service_returns_markdown_resource_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted markdown is returned exactly as stored."""
    prepared_markdown = (
        "---\ntitle: Test note\ntype: note\npermalink: main/notes/test-note\n---\n\n# Test note\n"
    )
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_query_view",
        AsyncMock(
            return_value=_view(note_content=_note_content(markdown_content=prepared_markdown))
        ),
    )

    resource = await _query_service().get_note_resource(
        project_external_id="main",
        entity_external_id=str(uuid4()),
    )

    assert resource is not None
    assert resource.content == prepared_markdown
    assert resource.content_type == "text/markdown"


@pytest.mark.asyncio
async def test_note_content_query_service_returns_body_only_resource_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Body-only note_content should not gain synthetic frontmatter on reads."""
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_query_view",
        AsyncMock(
            return_value=_view(
                entity=_entity(
                    entity_metadata={
                        "title": "ignored duplicate",
                        "type": "ignored duplicate",
                        "permalink": "ignored duplicate",
                        "status": "external",
                    }
                ),
                note_content=_note_content(markdown_content="# External body\n"),
            )
        ),
    )

    resource = await _query_service().get_note_resource(
        project_external_id="main",
        entity_external_id=str(uuid4()),
    )

    assert resource is not None
    assert resource.content_type == "text/markdown"
    assert resource.content == "# External body\n"


@pytest.mark.asyncio
async def test_note_content_query_service_returns_none_when_view_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing note_content views let callers continue through fallback paths."""
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_query_view",
        AsyncMock(return_value=None),
    )
    service = _query_service()

    assert (
        await service.get_note_entity_payload(
            project_external_id="main",
            entity_external_id=str(uuid4()),
        )
        is None
    )
    assert (
        await service.get_note_resource(
            project_external_id="main",
            entity_external_id=str(uuid4()),
        )
        is None
    )
