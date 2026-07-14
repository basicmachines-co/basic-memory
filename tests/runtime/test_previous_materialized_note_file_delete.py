from types import SimpleNamespace

from basic_memory.runtime.note_content import (
    RuntimePendingNoteFileDelete,
    plan_previous_materialized_note_file_delete,
)


def test_plan_previous_materialized_note_file_delete_uses_note_content_file_checksum():
    note_content = SimpleNamespace(file_checksum="old-file-checksum")

    cleanup = plan_previous_materialized_note_file_delete(
        project_id=7,
        entity_id=42,
        existing_file_path="notes/old.md",
        accepted_file_path="notes/new.md",
        current_note_content=note_content,
    )

    assert cleanup == RuntimePendingNoteFileDelete(
        project_id=7,
        entity_id=42,
        file_path="notes/old.md",
        file_checksum="old-file-checksum",
        # The accepted destination rides along so the local delete adapter can
        # skip a case-only rename that aliases old and new on disk (P0 guard).
        live_file_path="notes/new.md",
    )


def test_plan_previous_materialized_note_file_delete_skips_missing_materialized_checksum():
    assert (
        plan_previous_materialized_note_file_delete(
            project_id=7,
            entity_id=42,
            existing_file_path="notes/old.md",
            accepted_file_path="notes/new.md",
            current_note_content=None,
        )
        is None
    )
    assert (
        plan_previous_materialized_note_file_delete(
            project_id=7,
            entity_id=42,
            existing_file_path="notes/old.md",
            accepted_file_path="notes/new.md",
            current_note_content=SimpleNamespace(file_checksum=None),
        )
        is None
    )
