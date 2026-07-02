"""Tests for materializing prepared notes into a runtime content store."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest

from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_KIND_METADATA,
    NOTE_OBJECT_ACTOR_NAME_METADATA,
    NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA,
    NOTE_OBJECT_DB_CHECKSUM_METADATA,
    NOTE_OBJECT_DB_VERSION_METADATA,
    NOTE_OBJECT_ENTITY_ID_METADATA,
    NOTE_OBJECT_FILE_CHECKSUM_METADATA,
    NOTE_OBJECT_FILE_VERSION_METADATA,
    NOTE_OBJECT_SOURCE_METADATA,
    RuntimeFileConflictError,
    RuntimeNoteMaterializationJobRequest,
    plan_prepared_note_write,
    write_prepared_note_to_content_store,
)


@dataclass(frozen=True, slots=True)
class _FileMetadata:
    modified_at: datetime


class _ContentStore:
    def __init__(
        self,
        *,
        existing_checksum: str | None,
        written_checksum: str = "written-checksum",
        modified_at: datetime | None = None,
    ) -> None:
        self.existing_checksum = existing_checksum
        self.written_checksum = written_checksum
        self.modified_at = modified_at or datetime(2026, 6, 19, 16, 0, tzinfo=UTC)
        self.write_calls: list[tuple[str, str, dict[str, str] | None]] = []

    async def exists(self, path: str) -> bool:
        return self.existing_checksum is not None

    async def compute_checksum(self, path: str) -> str:
        if self.existing_checksum is None:
            raise AssertionError("compute_checksum should only be called for existing files")
        return self.existing_checksum

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> str:
        self.write_calls.append((path, content, metadata))
        return self.written_checksum

    async def get_file_metadata(self, path: str) -> _FileMetadata:
        return _FileMetadata(modified_at=self.modified_at)


def _request() -> RuntimeNoteMaterializationJobRequest:
    return RuntimeNoteMaterializationJobRequest(
        project_id=7,
        entity_id=42,
        db_version=4,
        db_checksum="db-checksum",
        actor_user_profile_id=UUID("33333333-3333-3333-3333-333333333333"),
        actor_kind="mcp_client",
        actor_name="Claude Code",
        source="mcp",
    )


@pytest.mark.asyncio
async def test_write_prepared_note_to_content_store_writes_metadata_and_returns_state() -> None:
    attempted_at = datetime(2026, 6, 19, 15, 59, tzinfo=UTC)
    modified_at = datetime(2026, 6, 19, 16, 0, tzinfo=UTC)
    prepared_write = plan_prepared_note_write(
        request=_request(),
        file_path="notes/a.md",
        markdown_content="# A note\n",
        previous_file_checksum="old-checksum",
        attempted_at=attempted_at,
    )
    content_store = _ContentStore(
        existing_checksum="old-checksum",
        written_checksum="new-checksum",
        modified_at=modified_at,
    )

    written_file = await write_prepared_note_to_content_store(content_store, prepared_write)

    assert written_file.file_path == "notes/a.md"
    assert written_file.file_checksum == "new-checksum"
    assert written_file.file_updated_at == modified_at
    assert content_store.write_calls == [
        (
            "notes/a.md",
            "# A note\n",
            {
                NOTE_OBJECT_ENTITY_ID_METADATA: "42",
                NOTE_OBJECT_DB_VERSION_METADATA: "4",
                NOTE_OBJECT_DB_CHECKSUM_METADATA: "db-checksum",
                NOTE_OBJECT_FILE_VERSION_METADATA: "4",
                NOTE_OBJECT_FILE_CHECKSUM_METADATA: "db-checksum",
                NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: (
                    "33333333-3333-3333-3333-333333333333"
                ),
                NOTE_OBJECT_ACTOR_KIND_METADATA: "mcp_client",
                NOTE_OBJECT_ACTOR_NAME_METADATA: "Claude Code",
                NOTE_OBJECT_SOURCE_METADATA: "mcp",
            },
        )
    ]


@pytest.mark.asyncio
async def test_write_prepared_note_to_content_store_rejects_unexpected_existing_file() -> None:
    prepared_write = plan_prepared_note_write(
        request=_request(),
        file_path="notes/a.md",
        markdown_content="# A note\n",
        previous_file_checksum=None,
        attempted_at=datetime(2026, 6, 19, 15, 59, tzinfo=UTC),
    )
    content_store = _ContentStore(existing_checksum="external-checksum")

    with pytest.raises(RuntimeFileConflictError):
        await write_prepared_note_to_content_store(content_store, prepared_write)

    assert content_store.write_calls == []
