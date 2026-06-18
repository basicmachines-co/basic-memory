"""Tests for portable indexing progress checkpoint models."""

from basic_memory.indexing.progress import IndexingResult, VectorSyncProgress


def test_vector_sync_progress_checkpoint_round_trip() -> None:
    progress = VectorSyncProgress(
        entity_ids=[11, 22, 22],
        next_index=5,
        entities_synced=2,
        entities_failed=1,
        failed_entity_ids=[22, 22],
        embedding_jobs_total=40,
        embed_seconds_total=12.3456,
        write_seconds_total=1.2345,
        elapsed_seconds=15.6789,
    )

    restored = VectorSyncProgress.from_checkpoint_state(progress.to_checkpoint_state())

    assert restored.entity_ids == [11, 22]
    assert restored.next_index == 2
    assert restored.entities_synced == 2
    assert restored.entities_failed == 1
    assert restored.failed_entity_ids == [22]
    assert restored.embedding_jobs_total == 40
    assert restored.embed_seconds_total == 12.346
    assert restored.write_seconds_total == 1.234
    assert restored.elapsed_seconds == 15.679
    assert restored.entities_total == 2


def test_vector_sync_progress_without_entity_ids_keeps_counters_only() -> None:
    progress = VectorSyncProgress(
        entity_ids=[11, 22],
        next_index=1,
        entities_synced=2,
        entities_failed=1,
        failed_entity_ids=[22],
        embedding_jobs_total=40,
        embed_seconds_total=12.0,
        write_seconds_total=1.0,
        elapsed_seconds=15.0,
    )

    compact = progress.without_entity_ids()

    assert compact.entity_ids == []
    assert compact.next_index == 1
    assert compact.entities_synced == 2
    assert compact.entities_failed == 1
    assert compact.failed_entity_ids == [22]


def test_vector_sync_progress_recovers_empty_progress_from_missing_or_invalid_state() -> None:
    missing = VectorSyncProgress.from_checkpoint_state(None)
    invalid = VectorSyncProgress.from_checkpoint_state({"entity_ids": "not a list"})

    assert missing == VectorSyncProgress()
    assert invalid == VectorSyncProgress()


def test_indexing_result_checkpoint_round_trip() -> None:
    result = IndexingResult(
        files_processed=3,
        files_unchanged=4,
        entities_created=5,
        entities_deleted=1,
        relations_resolved=7,
        semantic_vectors_synced=9,
        errors=[("a.md", "bad frontmatter"), ("b.md", "missing title")],
        total_duration_seconds=12.3456,
        semantic_vector_sync_seconds=4.5678,
        peak_rss_mib=512.9876,
        batch_count=2,
    )

    restored = IndexingResult.from_checkpoint_state(result.to_checkpoint_state())

    assert restored.files_processed == 3
    assert restored.files_unchanged == 4
    assert restored.entities_created == 5
    assert restored.entities_deleted == 1
    assert restored.relations_resolved == 7
    assert restored.semantic_vectors_synced == 9
    assert restored.errors == [
        ("a.md", "bad frontmatter"),
        ("b.md", "missing title"),
    ]
    assert restored.total_duration_seconds == 12.346
    assert restored.semantic_vector_sync_seconds == 4.568
    assert restored.peak_rss_mib == 512.988
    assert restored.batch_count == 2
    assert restored.total_errors == 2
    assert restored.success is False
    assert restored.files_per_second == 3 / 12.346
    assert restored.avg_batch_duration == 0.0


def test_indexing_result_reports_zero_rates_without_duration_or_batches() -> None:
    result = IndexingResult(files_processed=3)

    assert result.files_per_second == 0.0
    assert result.avg_batch_duration == 0.0


def test_indexing_result_normalizes_legacy_error_payloads() -> None:
    restored = IndexingResult.from_checkpoint_state(
        {
            "errors": [
                ["a.md", "bad frontmatter"],
                {"path": "b.md", "error": "missing title"},
                {"ignored": "shape"},
            ],
        }
    )

    assert restored.errors == [
        ("a.md", "bad frontmatter"),
        ("b.md", "missing title"),
    ]


def test_indexing_result_recovers_empty_result_from_missing_or_invalid_state() -> None:
    missing = IndexingResult.from_checkpoint_state(None)
    invalid = IndexingResult.from_checkpoint_state({"errors": "not a list"})

    assert missing == IndexingResult()
    assert invalid == IndexingResult()
