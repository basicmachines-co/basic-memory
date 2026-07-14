from dataclasses import dataclass

from basic_memory.runtime.note_content import (
    RuntimeNoteMaterializationJobRequest,
    RuntimePendingNoteFileDelete,
    note_content_matches_materialization_request,
    plan_note_materialization_cleanup_file_delete,
)


@dataclass(frozen=True, slots=True)
class _NoteContentVersion:
    db_version: int | str
    db_checksum: object


def _request(
    *,
    db_version: int = 4,
    db_checksum: str = "12345",
    cleanup_file_path: str | None = None,
    cleanup_file_checksum: str | None = None,
) -> RuntimeNoteMaterializationJobRequest:
    return RuntimeNoteMaterializationJobRequest(
        project_id=7,
        entity_id=42,
        db_version=db_version,
        db_checksum=db_checksum,
        cleanup_file_path=cleanup_file_path,
        cleanup_file_checksum=cleanup_file_checksum,
    )


def test_note_content_matches_materialization_request_accepts_matching_row():
    note_content = _NoteContentVersion(db_version=4, db_checksum="12345")

    assert note_content_matches_materialization_request(note_content, _request())


def test_note_content_matches_materialization_request_with_coerced_runtime_values():
    # Replayed job payloads can deliver a string db_version / non-str checksum;
    # matching coerces instead of silently treating the row as stale.
    note_content = _NoteContentVersion(db_version="4", db_checksum=12345)

    assert note_content_matches_materialization_request(note_content, _request())


def test_note_content_matches_materialization_request_rejects_stale_version_or_checksum():
    assert not note_content_matches_materialization_request(
        _NoteContentVersion(db_version=5, db_checksum="db-checksum"),
        _request(db_checksum="db-checksum"),
    )
    assert not note_content_matches_materialization_request(
        _NoteContentVersion(db_version=4, db_checksum="other-checksum"),
        _request(),
    )


def test_plan_note_materialization_cleanup_file_delete_uses_request_cleanup_marker():
    cleanup = plan_note_materialization_cleanup_file_delete(
        _request(
            cleanup_file_path="notes/old.md",
            cleanup_file_checksum="old-checksum",
        )
    )

    assert cleanup == RuntimePendingNoteFileDelete(
        project_id=7,
        entity_id=42,
        file_path="notes/old.md",
        file_checksum="old-checksum",
    )


def test_plan_note_materialization_cleanup_file_delete_skips_request_without_cleanup_path():
    assert plan_note_materialization_cleanup_file_delete(_request()) is None
