"""Tests for prepared entity field helpers."""

from datetime import UTC, datetime
from uuid import uuid4

from basic_memory.models import Entity
from basic_memory.services.entity_service import (
    PreparedEntityFields,
    apply_prepared_entity_fields,
)


def test_apply_prepared_entity_fields_updates_accepted_entity_state() -> None:
    now = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
    updated_at = datetime(2026, 4, 13, 13, 0, tzinfo=UTC)
    entity = Entity(
        id=42,
        external_id=str(uuid4()),
        title="Original",
        note_type="note",
        entity_metadata={"topic": "tests"},
        content_type="text/markdown",
        project_id=7,
        permalink="main/notes/original",
        file_path="notes/original.md",
        checksum=None,
        created_at=now,
        updated_at=now,
        created_by="creator",
        last_updated_by="creator",
    )

    apply_prepared_entity_fields(
        entity,
        PreparedEntityFields(
            title="Renamed",
            note_type="decision",
            entity_metadata={"status": "accepted"},
            content_type="text/markdown",
            permalink="main/journal/renamed",
            file_path="journal/renamed.md",
        ),
        updated_at=updated_at,
        user_profile_value="editor-123",
    )

    assert entity.title == "Renamed"
    assert entity.note_type == "decision"
    assert entity.entity_metadata == {"status": "accepted"}
    assert entity.content_type == "text/markdown"
    assert entity.permalink == "main/journal/renamed"
    assert entity.file_path == "journal/renamed.md"
    assert entity.updated_at == updated_at
    assert entity.last_updated_by == "editor-123"
