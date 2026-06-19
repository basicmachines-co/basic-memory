"""Tests for portable file-index content read planning."""

from basic_memory.indexing.file_index_planning import (
    FileIndexDecision,
    FileIndexDecisionStatus,
    FileIndexPlan,
    FileIndexPlanSummary,
    FileIndexTarget,
    build_file_index_plan,
    plan_file_index_target_from_current,
    plan_file_index_target_from_observed,
    plan_legacy_file_index_targets,
    summarize_file_index_plan,
)


def test_observed_checksum_match_plans_current_without_current_metadata() -> None:
    decision = plan_file_index_target_from_observed(
        FileIndexTarget(path="notes/current.md", observed_checksum="etag-current"),
        db_checksum="etag-current",
    )

    assert decision == FileIndexDecision(
        path="notes/current.md",
        status=FileIndexDecisionStatus.current,
        reason="file already indexed: notes/current.md",
    )


def test_observed_checksum_mismatch_defers_to_current_metadata() -> None:
    decision = plan_file_index_target_from_observed(
        FileIndexTarget(path="notes/dirty.md", observed_checksum="old-etag"),
        db_checksum="db-etag",
    )

    assert decision is None


def test_current_metadata_plans_missing_current_or_read() -> None:
    missing = plan_file_index_target_from_current(
        FileIndexTarget(path="notes/missing.md", observed_checksum="old-etag"),
        db_checksum="db-etag",
        current_checksum=None,
    )
    caught_up = plan_file_index_target_from_current(
        FileIndexTarget(path="notes/caught-up.md", observed_checksum="old-etag"),
        db_checksum="db-etag",
        current_checksum="db-etag",
    )
    dirty = plan_file_index_target_from_current(
        FileIndexTarget(path="notes/dirty.md", observed_checksum="old-etag"),
        db_checksum="db-etag",
        current_checksum="new-etag",
    )

    assert missing.status == FileIndexDecisionStatus.missing
    assert missing.reason == "file not found: notes/missing.md"
    assert caught_up.status == FileIndexDecisionStatus.current
    assert dirty == FileIndexDecision(
        path="notes/dirty.md",
        status=FileIndexDecisionStatus.read,
        reason="file needs indexing: notes/dirty.md",
    )


def test_build_file_index_plan_keeps_reads_in_paths_and_terminal_decisions() -> None:
    plan = build_file_index_plan(
        [
            FileIndexDecision(
                path="notes/read.md",
                status=FileIndexDecisionStatus.read,
                reason="file needs indexing: notes/read.md",
            ),
            FileIndexDecision(
                path="notes/current.md",
                status=FileIndexDecisionStatus.current,
                reason="file already indexed: notes/current.md",
            ),
            FileIndexDecision(
                path="notes/missing.md",
                status=FileIndexDecisionStatus.missing,
                reason="file not found: notes/missing.md",
            ),
        ]
    )

    assert plan == FileIndexPlan(
        paths_to_read=("notes/read.md",),
        decisions=(
            FileIndexDecision(
                path="notes/current.md",
                status=FileIndexDecisionStatus.current,
                reason="file already indexed: notes/current.md",
            ),
            FileIndexDecision(
                path="notes/missing.md",
                status=FileIndexDecisionStatus.missing,
                reason="file not found: notes/missing.md",
            ),
        ),
    )


def test_summarize_file_index_plan_counts_read_current_and_missing_targets() -> None:
    plan = FileIndexPlan(
        paths_to_read=("notes/read.md",),
        decisions=(
            FileIndexDecision(
                path="notes/current.md",
                status=FileIndexDecisionStatus.current,
                reason="file already indexed: notes/current.md",
            ),
            FileIndexDecision(
                path="notes/missing.md",
                status=FileIndexDecisionStatus.missing,
                reason="file not found: notes/missing.md",
            ),
        ),
    )

    assert summarize_file_index_plan(plan) == FileIndexPlanSummary(
        total_files=3,
        files_to_read=1,
        current_files=1,
        missing_files=1,
    )


def test_plan_legacy_file_index_targets_reads_all_paths_without_decisions() -> None:
    plan = plan_legacy_file_index_targets(
        [
            FileIndexTarget(path="notes/one.md"),
            FileIndexTarget(path="notes/two.md"),
        ]
    )

    assert plan == FileIndexPlan(
        paths_to_read=("notes/one.md", "notes/two.md"),
        decisions=(),
    )
