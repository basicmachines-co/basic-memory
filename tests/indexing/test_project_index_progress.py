"""Tests for portable project-index workflow progress state."""

import pytest

from basic_memory.indexing.project_index_progress import (
    ProjectIndexBatchCounterUpdate,
    ProjectIndexCounters,
    ProjectIndexFileOutcome,
    ProjectIndexFileOutcomeSummary,
    apply_project_index_batch_outcomes,
    apply_project_index_file_outcome,
    apply_project_index_file_outcomes,
    initial_project_index_counters,
    project_index_batch_count_from_metadata,
    project_index_counters_from_metadata,
    project_index_missing_batches_from_metadata,
    project_index_progress_text,
    project_index_recorded_batches_from_metadata,
    should_emit_project_index_progress_event,
    summarize_project_index_file_outcomes,
)


def test_project_index_counters_format_progress_text() -> None:
    counters = ProjectIndexCounters(
        total=5,
        processed=3,
        succeeded=2,
        missing=1,
        failed=0,
    )

    assert project_index_progress_text(counters) == "Indexed 3/5 files, 2 succeeded, 1 missing"
    assert (
        project_index_progress_text(
            ProjectIndexCounters(total=5, processed=5, succeeded=3, missing=1, failed=1)
        )
        == "Indexed 5/5 files, 3 succeeded, 1 missing, 1 failed"
    )
    assert project_index_progress_text(initial_project_index_counters(0)) == "No files found"


def test_project_index_progress_event_throttle_keeps_start_finish_and_intervals() -> None:
    assert should_emit_project_index_progress_event(
        ProjectIndexCounters(total=200, processed=1, succeeded=1, missing=0, failed=0)
    )
    assert should_emit_project_index_progress_event(
        ProjectIndexCounters(total=200, processed=50, succeeded=50, missing=0, failed=0)
    )
    assert should_emit_project_index_progress_event(
        ProjectIndexCounters(total=200, processed=200, succeeded=200, missing=0, failed=0)
    )
    assert not should_emit_project_index_progress_event(
        ProjectIndexCounters(total=200, processed=51, succeeded=51, missing=0, failed=0)
    )


def test_project_index_file_outcomes_update_counters() -> None:
    counters = ProjectIndexCounters(total=4, processed=0, succeeded=0, missing=0, failed=0)

    updated = apply_project_index_file_outcomes(
        counters,
        [
            ProjectIndexFileOutcome.processed,
            ProjectIndexFileOutcome.current,
            ProjectIndexFileOutcome.missing,
            ProjectIndexFileOutcome.failed,
        ],
    )

    assert updated == ProjectIndexCounters(
        total=4,
        processed=4,
        succeeded=2,
        missing=1,
        failed=1,
    )
    assert apply_project_index_file_outcome(
        counters,
        ProjectIndexFileOutcome.current,
    ) == ProjectIndexCounters(total=4, processed=1, succeeded=1, missing=0, failed=0)


def test_project_index_file_outcomes_summarize_batch_result_counts() -> None:
    summary = summarize_project_index_file_outcomes(
        [
            ProjectIndexFileOutcome.processed,
            ProjectIndexFileOutcome.current,
            ProjectIndexFileOutcome.missing,
            ProjectIndexFileOutcome.failed,
        ],
    )

    assert summary == ProjectIndexFileOutcomeSummary(
        total_files=4,
        processed_files=2,
        missing_files=1,
        failed_files=1,
    )


def test_project_index_batch_outcomes_record_once_and_report_completion_gate() -> None:
    counters = ProjectIndexCounters(total=3, processed=1, succeeded=1, missing=0, failed=0)

    update = apply_project_index_batch_outcomes(
        counters=counters,
        recorded_batch_indexes=[0],
        batch_index=1,
        batch_count=2,
        outcomes=[ProjectIndexFileOutcome.missing, ProjectIndexFileOutcome.failed],
    )

    assert update == ProjectIndexBatchCounterUpdate(
        counters=ProjectIndexCounters(
            total=3,
            processed=3,
            succeeded=1,
            missing=1,
            failed=1,
        ),
        recorded_batch_indexes=[0, 1],
        already_recorded=False,
        all_batches_recorded=True,
    )
    assert update.is_complete


def test_project_index_batch_outcomes_skip_already_recorded_batch() -> None:
    counters = ProjectIndexCounters(total=2, processed=1, succeeded=1, missing=0, failed=0)

    update = apply_project_index_batch_outcomes(
        counters=counters,
        recorded_batch_indexes=[0],
        batch_index=0,
        batch_count=1,
        outcomes=[ProjectIndexFileOutcome.failed],
    )

    assert update.counters == counters
    assert update.recorded_batch_indexes == [0]
    assert update.already_recorded is True
    assert update.all_batches_recorded is True
    assert update.is_complete is False


def test_project_index_metadata_extracts_counters_recorded_batches_and_missing_batches() -> None:
    metadata: dict[str, object] = {
        "discovery": {
            "batch_count": 4,
            "batch_size": 50,
        },
        "counters": {
            "total": 125,
            "processed": 75,
            "succeeded": 70,
            "missing": 3,
            "failed": 2,
        },
        "recorded_batches": [2, 0],
    }

    counters = project_index_counters_from_metadata(metadata, workflow_id="wf-123")
    missing = project_index_missing_batches_from_metadata(metadata)

    assert counters == ProjectIndexCounters(
        total=125,
        processed=75,
        succeeded=70,
        missing=3,
        failed=2,
    )
    assert counters.to_metadata() == {
        "total": 125,
        "processed": 75,
        "succeeded": 70,
        "missing": 3,
        "failed": 2,
    }
    assert project_index_batch_count_from_metadata(metadata) == 4
    assert project_index_recorded_batches_from_metadata(metadata) == [0, 2]
    assert missing.missing_batch_indexes == [1, 3]
    assert missing.recorded_batch_indexes == [0, 2]
    assert missing.legacy_missing_batch_count is False


def test_project_index_metadata_allows_legacy_discovery_without_batch_count() -> None:
    metadata: dict[str, object] = {
        "discovery": {
            "total_files": 304,
        },
        "recorded_batches": [],
    }

    missing = project_index_missing_batches_from_metadata(metadata)

    assert project_index_batch_count_from_metadata(metadata) is None
    assert missing.missing_batch_indexes == []
    assert missing.recorded_batch_indexes == []
    assert missing.legacy_missing_batch_count is True


def test_project_index_metadata_rejects_invalid_boundary_shapes() -> None:
    with pytest.raises(RuntimeError, match="discovery metadata is invalid"):
        project_index_missing_batches_from_metadata({"discovery": {"batch_count": True}})

    with pytest.raises(RuntimeError, match="discovery metadata is invalid"):
        project_index_missing_batches_from_metadata({"discovery": {}})

    with pytest.raises(RuntimeError, match="counters"):
        project_index_counters_from_metadata({"counters": {"total": 1}}, workflow_id="wf-123")
