from __future__ import annotations

from dataclasses import dataclass

from basic_memory.runtime import (
    RuntimeAcceptedNoteContentWritePlan,
    RuntimePendingNoteFileDelete,
    RuntimePendingNoteMaterialization,
    next_runtime_note_content_version,
    plan_accepted_note_content_write,
    plan_accepted_note_materialization_change,
    plan_pending_note_materialization,
)


@dataclass(frozen=True, slots=True)
class _NoteContentState:
    db_version: int | str
    db_checksum: str
    last_source: str | None
    file_checksum: str | None = None


def test_plan_pending_note_materialization_uses_fallback_source_when_missing() -> None:
    cleanup = RuntimePendingNoteFileDelete(
        project_id=7,
        entity_id=42,
        file_path="notes/old.md",
        file_checksum="old-checksum",
    )

    materialization = plan_pending_note_materialization(
        project_id=7,
        entity_id=42,
        note_content=_NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            last_source=None,
        ),
        fallback_source="api",
        actor_kind="mcp_client",
        actor_name="Claude Code",
        cleanup_after_write=cleanup,
    )

    assert materialization == RuntimePendingNoteMaterialization(
        project_id=7,
        entity_id=42,
        db_version=4,
        db_checksum="db-checksum",
        actor_kind="mcp_client",
        actor_name="Claude Code",
        source="api",
        cleanup_after_write=cleanup,
    )


def test_plan_pending_note_materialization_prefers_note_source() -> None:
    materialization = plan_pending_note_materialization(
        project_id=7,
        entity_id=42,
        note_content=_NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            last_source="mcp",
        ),
        fallback_source="api",
    )

    assert materialization.source == "mcp"


def test_next_runtime_note_content_version_starts_new_rows_at_one() -> None:
    assert next_runtime_note_content_version(None) == 1


def test_next_runtime_note_content_version_advances_existing_rows() -> None:
    assert (
        next_runtime_note_content_version(
            _NoteContentState(db_version=4, db_checksum="db-checksum", last_source=None)
        )
        == 5
    )


def test_next_runtime_note_content_version_accepts_db_string_values() -> None:
    assert (
        next_runtime_note_content_version(
            _NoteContentState(db_version="4", db_checksum="db-checksum", last_source=None)
        )
        == 5
    )


def test_plan_accepted_note_content_write_starts_new_rows_without_cleanup() -> None:
    assert plan_accepted_note_content_write(
        project_id=7,
        entity_id=42,
        accepted_file_path="notes/new.md",
    ) == RuntimeAcceptedNoteContentWritePlan(db_version=1)


def test_plan_accepted_note_content_write_advances_and_cleans_moved_materialized_file() -> None:
    plan = plan_accepted_note_content_write(
        project_id=7,
        entity_id=42,
        existing_file_path="notes/old.md",
        accepted_file_path="notes/new.md",
        current_note_content=_NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            last_source="api",
            file_checksum="old-file-checksum",
        ),
    )

    assert plan == RuntimeAcceptedNoteContentWritePlan(
        db_version=5,
        previous_file_delete=RuntimePendingNoteFileDelete(
            project_id=7,
            entity_id=42,
            file_path="notes/old.md",
            file_checksum="old-file-checksum",
        ),
    )


def test_plan_accepted_note_content_write_skips_unmaterialized_file_cleanup() -> None:
    assert plan_accepted_note_content_write(
        project_id=7,
        entity_id=42,
        existing_file_path="notes/old.md",
        accepted_file_path="notes/new.md",
        current_note_content=_NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            last_source="api",
            file_checksum=None,
        ),
    ) == RuntimeAcceptedNoteContentWritePlan(db_version=5)


def test_plan_accepted_note_materialization_change_wraps_response_and_marker() -> None:
    cleanup = RuntimePendingNoteFileDelete(
        project_id=7,
        entity_id=42,
        file_path="notes/old.md",
        file_checksum="old-checksum",
    )
    payload = {"external_id": "note-123", "file_write_status": "pending"}

    accepted = plan_accepted_note_materialization_change(
        status_code=200,
        payload=payload,
        project_id=7,
        entity_id=42,
        note_content=_NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            last_source=None,
        ),
        fallback_source="api",
        actor_kind="mcp_client",
        actor_name="Claude Code",
        cleanup_after_write=cleanup,
    )

    assert accepted.status_code == 200
    assert accepted.payload is payload
    assert accepted.file_delete is None
    assert accepted.materialization == RuntimePendingNoteMaterialization(
        project_id=7,
        entity_id=42,
        db_version=4,
        db_checksum="db-checksum",
        actor_kind="mcp_client",
        actor_name="Claude Code",
        source="api",
        cleanup_after_write=cleanup,
    )
