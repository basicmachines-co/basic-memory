"""Tests for portable sample-note initialization results."""

from basic_memory.runtime.sample_notes import RuntimeSampleNotesInitializationResult


def test_sample_notes_initialization_result_tracks_counts_for_workflow_result() -> None:
    """Sample-note initialization results should be immutable workflow payload values."""
    result = (
        RuntimeSampleNotesInitializationResult.started()
        .with_project_created(True)
        .record_note_created()
        .record_note_created()
        .record_note_failed()
        .record_index_job_enqueued()
    )

    assert result.as_workflow_result() == {
        "project_created": 1,
        "notes_created": 2,
        "notes_failed": 1,
        "index_jobs_enqueued": 1,
    }


def test_sample_notes_initialization_result_preserves_not_started_shape() -> None:
    """Missing prerequisites still serialize as the legacy empty workflow result."""
    result = RuntimeSampleNotesInitializationResult.not_started()

    assert result.as_workflow_result() == {}
