"""Tests for portable indexing progress checkpoint models."""

from dataclasses import dataclass, field

from basic_memory.indexing.progress import (
    IndexingResult,
    VectorSyncProgress,
    apply_vector_sync_batch_result,
    initialize_vector_sync_progress,
)


# Not frozen: VectorSyncBatchSummary declares plain (writable) attribute members.
@dataclass(slots=True)
class BatchSummary:
    """Small fake proving progress updates only need a narrow batch protocol."""

    entities_synced: int
    entities_failed: int
    failed_entity_ids: list[int] = field(default_factory=list)
    embedding_jobs_total: int = 0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0


def test_vector_sync_progress_checkpoint_state_shape_is_stable() -> None:
    """The dumped checkpoint document must stay byte-identical across releases."""
    progress = VectorSyncProgress(
        entity_ids=[11, 22],
        next_index=2,
        entities_synced=2,
        entities_failed=1,
        failed_entity_ids=[22],
        embedding_jobs_total=40,
        embed_seconds_total=12.346,
        write_seconds_total=1.234,
        elapsed_seconds=15.679,
    )

    state = progress.to_checkpoint_state()

    expected = {
        "entity_ids": [11, 22],
        "next_index": 2,
        "entities_synced": 2,
        "entities_failed": 1,
        "failed_entity_ids": [22],
        "embedding_jobs_total": 40,
        "embed_seconds_total": 12.346,
        "write_seconds_total": 1.234,
        "elapsed_seconds": 15.679,
        "entities_total": 2,
    }
    assert state == expected
    assert list(state) == list(expected)
    # Old checkpoints carry the computed entities_total field; restoring must
    # ignore it and rebuild the identical progress value.
    assert VectorSyncProgress.from_checkpoint_state(expected) == progress


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


def test_initialize_vector_sync_progress_prefers_resume_entity_plan() -> None:
    resume = VectorSyncProgress(
        entity_ids=[11, 22],
        next_index=5,
        entities_synced=2,
        entities_failed=1,
        failed_entity_ids=[22],
        embedding_jobs_total=40,
        embed_seconds_total=12.0,
        write_seconds_total=1.0,
        elapsed_seconds=15.0,
    )

    progress = initialize_vector_sync_progress(
        entity_ids=[33],
        resume_progress=resume,
    )

    assert progress.entity_ids == [11, 22]
    assert progress.next_index == 2
    assert progress.entities_synced == 2
    assert progress.entities_failed == 1
    assert progress.failed_entity_ids == [22]
    assert progress.embedding_jobs_total == 40
    assert progress.elapsed_seconds == 15.0


def test_initialize_vector_sync_progress_uses_entity_ids_without_resume_plan() -> None:
    progress = initialize_vector_sync_progress(
        entity_ids=[33, 44],
        resume_progress=None,
    )

    assert progress.entity_ids == [33, 44]
    assert progress.next_index == 0
    assert progress.entities_synced == 0


def test_apply_vector_sync_batch_result_updates_progress_and_reports_new_failures() -> None:
    progress = VectorSyncProgress(
        entity_ids=[11, 22, 33],
        next_index=1,
        entities_synced=1,
        entities_failed=1,
        failed_entity_ids=[22],
        embedding_jobs_total=4,
        embed_seconds_total=1.5,
        write_seconds_total=0.5,
    )

    new_failed_entity_ids = apply_vector_sync_batch_result(
        progress,
        BatchSummary(
            entities_synced=2,
            entities_failed=2,
            failed_entity_ids=[22, 33],
            embedding_jobs_total=6,
            embed_seconds_total=2.0,
            write_seconds_total=0.75,
        ),
        next_index=3,
        elapsed_seconds=8.5,
    )

    assert progress.next_index == 3
    assert progress.entities_synced == 3
    assert progress.entities_failed == 3
    assert progress.embedding_jobs_total == 10
    assert progress.embed_seconds_total == 3.5
    assert progress.write_seconds_total == 1.25
    assert progress.elapsed_seconds == 8.5
    assert progress.failed_entity_ids == [22, 33]
    assert new_failed_entity_ids == [33]


def test_indexing_result_checkpoint_state_shape_is_stable() -> None:
    """The dumped checkpoint document must stay byte-identical across releases."""
    result = IndexingResult(
        files_processed=3,
        files_unchanged=4,
        entities_created=5,
        entities_deleted=1,
        relations_resolved=7,
        semantic_vectors_synced=9,
        errors=[("a.md", "bad frontmatter")],
        total_duration_seconds=12.346,
        semantic_vector_sync_seconds=4.568,
        peak_rss_mib=512.988,
        batch_count=2,
    )

    state = result.to_checkpoint_state()

    expected = {
        "files_processed": 3,
        "files_unchanged": 4,
        "entities_created": 5,
        "entities_updated": 0,
        "entities_deleted": 1,
        "files_moved": 0,
        "forward_refs_resolved": 0,
        "relations_resolved": 7,
        "relations_unresolved": 0,
        "search_indexed": 0,
        "semantic_vector_entities_total": 0,
        "semantic_vectors_synced": 9,
        "semantic_vectors_failed": 0,
        "errors": [["a.md", "bad frontmatter"]],
        "total_duration_seconds": 12.346,
        "change_detection_seconds": 0.0,
        "s3_download_seconds": 0.0,
        "file_processing_seconds": 0.0,
        "relation_resolution_seconds": 0.0,
        "search_indexing_seconds": 0.0,
        "semantic_vector_sync_seconds": 4.568,
        "semantic_vector_embed_seconds": 0.0,
        "semantic_vector_write_seconds": 0.0,
        "peak_rss_mib": 512.988,
        "batch_count": 2,
    }
    assert state == expected
    assert list(state) == list(expected)
    assert IndexingResult.from_checkpoint_state(expected) == result


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
