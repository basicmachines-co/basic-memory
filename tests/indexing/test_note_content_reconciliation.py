"""Tests for note-content file/DB reconciliation planning."""

from datetime import UTC, datetime

from basic_memory.indexing.note_content_reconciliation import (
    AcceptedNoteContentVersion,
    MaterializedNoteContentFile,
    NoteContentBootstrap,
    NoteContentFileObserved,
    NoteContentFileSynced,
    NoteContentMaterializedCurrent,
    NoteContentMaterializedStale,
    NoteContentMaterializationStatusUpdate,
    NoteContentPromoted,
    NoteContentState,
    ObservedNoteContent,
    plan_note_content_materialization_publish,
    plan_note_content_materialization_status,
    plan_note_content_reconciliation,
)


def _observed(checksum: str = "observed-checksum") -> ObservedNoteContent:
    return ObservedNoteContent(
        markdown_content="# Observed\n",
        checksum=checksum,
        observed_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        source="index",
    )


def test_plan_bootstraps_missing_note_content() -> None:
    plan = plan_note_content_reconciliation(None, _observed())

    assert plan == NoteContentBootstrap(
        markdown_content="# Observed\n",
        db_version=1,
        db_checksum="observed-checksum",
        file_version=1,
        file_checksum="observed-checksum",
        file_write_status="synced",
        last_source="index",
        updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        file_updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


def test_plan_marks_file_synced_when_observed_checksum_matches_db() -> None:
    plan = plan_note_content_reconciliation(
        NoteContentState(
            db_version=7,
            db_checksum="db-checksum",
            file_version=6,
            file_checksum="old-file-checksum",
        ),
        _observed("db-checksum"),
    )

    assert plan == NoteContentFileSynced(
        markdown_content="# Observed\n",
        file_version=7,
        file_checksum="db-checksum",
        file_write_status="synced",
        file_updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


def test_plan_refreshes_file_observation_when_db_is_ahead() -> None:
    plan = plan_note_content_reconciliation(
        NoteContentState(
            db_version=9,
            db_checksum="new-db-checksum",
            file_version=8,
            file_checksum="old-file-checksum",
        ),
        _observed("old-file-checksum"),
    )

    assert plan == NoteContentFileObserved(
        file_version=8,
        file_checksum="old-file-checksum",
        file_updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
    )


def test_plan_promotes_external_file_change_after_latest_known_version() -> None:
    plan = plan_note_content_reconciliation(
        NoteContentState(
            db_version=3,
            db_checksum="db-checksum",
            file_version=5,
            file_checksum="materialized-file-checksum",
        ),
        _observed("external-change-checksum"),
    )

    assert plan == NoteContentPromoted(
        markdown_content="# Observed\n",
        db_version=6,
        db_checksum="external-change-checksum",
        file_version=6,
        file_checksum="external-change-checksum",
        file_write_status="synced",
        last_source="index",
        updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        file_updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


def test_plan_materialization_publish_marks_current_payload_synced() -> None:
    attempted_at = datetime(2026, 6, 18, 12, 29, tzinfo=UTC)
    file_updated_at = datetime(2026, 6, 18, 12, 30, tzinfo=UTC)

    plan = plan_note_content_materialization_publish(
        current=NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            file_version=3,
            file_checksum="old-file-checksum",
        ),
        written=MaterializedNoteContentFile(
            db_version=4,
            db_checksum="db-checksum",
            file_checksum="written-file-checksum",
            file_updated_at=file_updated_at,
            attempted_at=attempted_at,
        ),
    )

    assert plan == NoteContentMaterializedCurrent(
        file_version=4,
        file_checksum="written-file-checksum",
        file_write_status="synced",
        file_updated_at=file_updated_at,
        last_materialization_error=None,
        last_materialization_attempt_at=attempted_at,
    )


def test_plan_materialization_publish_leaves_newer_db_version_pending() -> None:
    attempted_at = datetime(2026, 6, 18, 12, 29, tzinfo=UTC)
    file_updated_at = datetime(2026, 6, 18, 12, 30, tzinfo=UTC)

    plan = plan_note_content_materialization_publish(
        current=NoteContentState(
            db_version=5,
            db_checksum="newer-db-checksum",
            file_version=3,
            file_checksum="old-file-checksum",
        ),
        written=MaterializedNoteContentFile(
            db_version=4,
            db_checksum="db-checksum",
            file_checksum="written-file-checksum",
            file_updated_at=file_updated_at,
            attempted_at=attempted_at,
        ),
    )

    assert plan == NoteContentMaterializedStale(
        file_version=4,
        file_checksum="written-file-checksum",
        file_write_status="pending",
        file_updated_at=file_updated_at,
        last_materialization_error=None,
        last_materialization_attempt_at=attempted_at,
    )


def test_plan_materialization_status_marks_current_payload_failed() -> None:
    attempted_at = datetime(2026, 6, 18, 12, 29, tzinfo=UTC)

    plan = plan_note_content_materialization_status(
        current=NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            file_version=3,
            file_checksum="old-file-checksum",
        ),
        accepted=AcceptedNoteContentVersion(
            db_version=4,
            db_checksum="db-checksum",
        ),
        file_write_status="failed",
        attempted_at=attempted_at,
        error_message="write failed",
    )

    assert plan == NoteContentMaterializationStatusUpdate(
        file_write_status="failed",
        file_checksum=None,
        last_materialization_error="write failed",
        last_materialization_attempt_at=attempted_at,
    )


def test_plan_materialization_status_records_current_conflict_checksum() -> None:
    attempted_at = datetime(2026, 6, 18, 12, 29, tzinfo=UTC)

    plan = plan_note_content_materialization_status(
        current=NoteContentState(
            db_version=4,
            db_checksum="db-checksum",
            file_version=3,
            file_checksum="old-file-checksum",
        ),
        accepted=AcceptedNoteContentVersion(
            db_version=4,
            db_checksum="db-checksum",
        ),
        file_write_status="external_change_detected",
        attempted_at=attempted_at,
        actual_file_checksum="external-file-checksum",
        error_message="Refusing to overwrite unexpected file",
    )

    assert plan == NoteContentMaterializationStatusUpdate(
        file_write_status="external_change_detected",
        file_checksum="external-file-checksum",
        last_materialization_error="Refusing to overwrite unexpected file",
        last_materialization_attempt_at=attempted_at,
    )


def test_plan_materialization_status_skips_stale_payload() -> None:
    attempted_at = datetime(2026, 6, 18, 12, 29, tzinfo=UTC)

    plan = plan_note_content_materialization_status(
        current=NoteContentState(
            db_version=5,
            db_checksum="newer-db-checksum",
            file_version=4,
            file_checksum="newer-file-checksum",
        ),
        accepted=AcceptedNoteContentVersion(
            db_version=4,
            db_checksum="db-checksum",
        ),
        file_write_status="failed",
        attempted_at=attempted_at,
        error_message="write failed",
    )

    assert plan is None
