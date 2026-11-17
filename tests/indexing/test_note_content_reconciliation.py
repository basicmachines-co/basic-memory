"""Tests for note-content file/DB reconciliation planning."""

from datetime import UTC, datetime

import pytest

from basic_memory.indexing.note_content_reconciliation import (
    AcceptedNoteContentLineage,
    AcceptedNoteContentVersion,
    MaterializedNoteContentFile,
    NoteContentBootstrap,
    NoteContentFileObserved,
    NoteContentFileSynced,
    NoteContentMaterializedCurrent,
    NoteContentMaterializedStale,
    NoteContentMaterializationStatusUpdate,
    NoteContentPromoted,
    NoteContentReconciliationDeferred,
    NoteContentReconciliationResult,
    NoteContentState,
    NoteContentWriteStatus,
    ObservedNoteContent,
    bootstrap_note_content_plan,
    plan_existing_note_content_reconciliation,
    plan_note_content_materialization_publish,
    plan_note_content_materialization_status,
)


def test_reconciliation_result_requires_generation_only_for_current_status() -> None:
    assert NoteContentReconciliationResult.current(3).generation == 3
    assert NoteContentReconciliationResult.stale().generation is None
    assert NoteContentReconciliationResult.deferred().generation is None

    with pytest.raises(ValueError, match="Only current"):
        NoteContentReconciliationResult(status="current")
    with pytest.raises(ValueError, match="positive"):
        NoteContentReconciliationResult.current(0)


def _observed(checksum: str = "observed-checksum") -> ObservedNoteContent:
    return ObservedNoteContent(
        markdown_content="# Observed\n",
        checksum=checksum,
        observed_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        source="index",
    )


def test_plan_bootstraps_missing_note_content() -> None:
    plan = bootstrap_note_content_plan(observed=_observed())

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
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=7,
            db_checksum="db-checksum",
            file_version=6,
            file_checksum="old-file-checksum",
        ),
        observed=_observed("db-checksum"),
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
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=9,
            db_checksum="new-db-checksum",
            file_version=8,
            file_checksum="old-file-checksum",
        ),
        observed=_observed("old-file-checksum"),
    )

    assert plan == NoteContentFileObserved(
        file_version=8,
        file_checksum="old-file-checksum",
        file_updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
    )


def test_plan_defers_external_file_change_while_file_state_is_not_synced() -> None:
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=3,
            db_checksum="db-checksum",
            file_version=5,
            file_checksum="materialized-file-checksum",
            file_write_status="pending",
        ),
        observed=_observed("external-change-checksum"),
    )

    assert plan == NoteContentReconciliationDeferred()


def test_plan_promotes_external_file_change_from_synced_state() -> None:
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=5,
            db_checksum="db-checksum",
            file_version=5,
            file_checksum="db-checksum",
            file_write_status="synced",
        ),
        observed=_observed("external-change-checksum"),
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


@pytest.mark.parametrize(
    ("file_write_status", "file_checksum"),
    [
        # The generated note is still queued for its first write to storage.
        ("pending", None),
        # Its first write found the observed file already in storage and refused.
        ("external_change_detected", "observed-checksum"),
    ],
)
def test_plan_promotes_observed_file_over_projector_note_that_never_reached_storage(
    file_write_status: NoteContentWriteStatus,
    file_checksum: str | None,
) -> None:
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=1,
            db_checksum="generated-index-checksum",
            file_version=None,
            file_checksum=file_checksum,
            file_write_status=file_write_status,
            last_source="wiki_projector",
        ),
        observed=_observed("observed-checksum"),
    )

    assert plan == NoteContentPromoted(
        markdown_content="# Observed\n",
        db_version=2,
        db_checksum="observed-checksum",
        file_version=2,
        file_checksum="observed-checksum",
        file_write_status="synced",
        last_source="index",
        updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        file_updated_at=datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


@pytest.mark.parametrize("last_source", ["api", "mcp", "index", None])
@pytest.mark.parametrize("file_write_status", ["pending", "external_change_detected"])
def test_plan_defers_observed_file_over_unwritten_note_from_other_sources(
    last_source: str | None,
    file_write_status: NoteContentWriteStatus,
) -> None:
    """Only a generated projector note yields to storage before its first write."""
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=1,
            db_checksum="accepted-checksum",
            file_version=None,
            file_checksum=None,
            file_write_status=file_write_status,
            last_source=last_source,
        ),
        observed=_observed("observed-checksum"),
    )

    assert plan == NoteContentReconciliationDeferred()


@pytest.mark.parametrize("file_write_status", ["pending", "external_change_detected"])
def test_plan_defers_observed_file_over_projector_note_already_in_storage(
    file_write_status: NoteContentWriteStatus,
) -> None:
    """A projector note that reached storage keeps the ordinary stable-state rule."""
    plan = plan_existing_note_content_reconciliation(
        current=NoteContentState(
            db_version=3,
            db_checksum="regenerated-index-checksum",
            file_version=2,
            file_checksum="previous-index-checksum",
            file_write_status=file_write_status,
            last_source="wiki_projector",
        ),
        observed=_observed("observed-checksum"),
    )

    assert plan == NoteContentReconciliationDeferred()


@pytest.mark.parametrize(
    ("newest", "matched", "superseded"),
    [
        (None, None, False),  # no accepted history: a disk-only note
        (4, None, False),  # fresh bytes no accepted write ever produced
        (4, 4, False),  # bytes of the newest accepted version
        (4, 2, True),  # bytes of a version a newer accepted write replaced
    ],
)
def test_accepted_lineage_flags_only_superseded_observations(
    newest: int | None,
    matched: int | None,
    superseded: bool,
) -> None:
    lineage = AcceptedNoteContentLineage(
        newest_accepted_db_version=newest,
        matched_accepted_db_version=matched,
    )

    assert lineage.observation_is_superseded is superseded


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
