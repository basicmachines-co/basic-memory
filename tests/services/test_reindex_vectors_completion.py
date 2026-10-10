"""Project reindex must finish bounded vector shards without claiming partial success."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.semantic_chunking import build_vector_chunk_records
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.runtime.vector_sync import VectorSyncBatchResult
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService
from basic_memory.services.search_service import SearchService


class RecordingEmbeddingProvider:
    """Use deterministic embeddings with no model download or network calls."""

    model_name = "reindex-test"
    dimensions = 4

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[0.0, 0.0, 0.0, 1.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("sections", [300, 600])
@pytest.mark.parametrize("force_full", [False, True])
@pytest.mark.parametrize("small_first", [False, True])
async def test_reindex_drains_real_repository_shards(
    search_service: SearchService,
    entity_service: EntityService,
    app_config: BasicMemoryConfig,
    monkeypatch: pytest.MonkeyPatch,
    sections: int,
    force_full: bool,
    small_first: bool,
) -> None:
    """Exercise >256 and >512 actual chunks, including pre-existing partial vectors."""
    provider = RecordingEmbeddingProvider()
    repository_type = (
        PostgresSearchRepository
        if app_config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    repository = repository_type(
        search_service.session_maker,
        project_id=search_service.repository.project_id,
        app_config=app_config,
        embedding_provider=provider,
    )
    service = SearchService(
        repository,
        search_service.entity_repository,
        search_service.file_service,
        search_service.session_maker,
    )
    await service.init_search_index()
    content = "\n\n".join(
        f"## Section {index}\n" + f"Unique section {index}: " + "word " * 170
        for index in range(sections)
    )
    large, _ = await entity_service.create_or_update_entity(
        EntitySchema(title="Large", note_type="note", content=content)
    )
    small, _ = await entity_service.create_or_update_entity(
        EntitySchema(title="Small", note_type="note", content="A small note.")
    )
    await service.index_entity(large)
    await service.index_entity(small)
    initial = await service.sync_entity_vectors_batch([large.id])
    assert initial.entities_deferred == 1
    assert initial.deferred_entity_ids == (large.id,)
    assert initial.chunks_total > sections
    assert initial.embedding_jobs_total == 256
    initial_text_count = len(provider.embedded_texts)
    provider.embedded_texts.clear()

    # find_all has no ordering contract. Exercise both valid orders explicitly.
    monkeypatch.setattr(
        service.entity_repository,
        "find_all",
        AsyncMock(return_value=[small, large] if small_first else [large, small]),
    )
    batches: list[list[int]] = []
    sync_batch = service.sync_entity_vectors_batch

    async def recording_sync(
        entity_ids: list[int], progress_callback=None
    ) -> VectorSyncBatchResult:
        batches.append(entity_ids.copy())
        return await sync_batch(entity_ids, progress_callback)

    monkeypatch.setattr(service, "sync_entity_vectors_batch", recording_sync)
    clear = AsyncMock(wraps=repository.delete_project_vector_rows)
    reconcile = AsyncMock(wraps=repository.reconcile_vector_index)
    monkeypatch.setattr(repository, "delete_project_vector_rows", clear)
    monkeypatch.setattr(repository, "reconcile_vector_index", reconcile)
    progress: list[tuple[int, int, int]] = []
    stats = await service.reindex_vectors(
        progress_callback=lambda *event: progress.append(event), force_full=force_full
    )

    assert stats["total_entities"] == stats["embedded"] == 2
    assert stats["errors"] == stats["deferred"] == stats["skipped"] == 0
    assert set(batches[0]) == {large.id, small.id}
    assert all(batch == [large.id] for batch in batches[1:])
    remaining_large_chunks = initial.chunks_total - (0 if force_full else initial_text_count)
    assert [event[1:] for event in progress] == [(1, 2), (2, 2)]
    assert {event[0] for event in progress} == {large.id, small.id}
    if remaining_large_chunks > 256:
        # Only a later-pass completion has a required position in the sequence.
        assert [event[0] for event in progress] == [small.id, large.id]
    assert clear.await_count == int(force_full)
    reconcile.assert_awaited_once()
    expected_embeddings = initial.chunks_total + 1 - (0 if force_full else initial_text_count)
    assert len(provider.embedded_texts) == expected_embeddings
    async with db.scoped_session(service.session_maker) as session:
        rows = await session.execute(
            text(
                "SELECT COUNT(*), SUM(CASE WHEN embedding_status = 'ready' THEN 1 ELSE 0 END) "
                "FROM search_vector_chunks WHERE project_id = :project_id"
            ),
            {"project_id": repository.project_id},
        )
        assert rows.one() == (initial.chunks_total + 1, initial.chunks_total + 1)
    for entity_id in [large.id, small.id]:
        source_rows = await repository.get_entity_search_rows(entity_id)
        expected_keys = {
            record["chunk_key"] for record in build_vector_chunk_records(source_rows).records
        }
        manifest = await repository.get_entity_chunk_manifest(entity_id)
        assert {row.chunk_key for row in manifest} == expected_keys
        assert all(row.embedding_status == "ready" for row in manifest)
        assert await repository.get_entity_physical_chunk_keys(entity_id) == expected_keys
    completed = await service.sync_entity_vectors_batch([large.id, small.id])
    assert completed.entities_deferred == 0
    assert completed.embedding_jobs_total == 0
    assert completed.chunks_skipped == completed.chunks_total
    assert len(provider.embedded_texts) == expected_embeddings


def configure_reindex(
    service: SearchService, monkeypatch: pytest.MonkeyPatch, entity_ids: list[int]
) -> AsyncMock:
    """Isolate orchestration from repository IO for failure injection."""
    monkeypatch.setattr(
        service.repository, "semantic_effectively_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        service.entity_repository,
        "find_all",
        AsyncMock(return_value=[SimpleNamespace(id=entity_id) for entity_id in entity_ids]),
    )
    monkeypatch.setattr(service, "_purge_stale_search_rows", AsyncMock())
    reconcile = AsyncMock()
    monkeypatch.setattr(service.repository, "reconcile_vector_index", reconcile)
    return reconcile


@pytest.mark.asyncio
async def test_reindex_mixed_outcomes_and_later_error(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = configure_reindex(search_service, monkeypatch, [1, 2, 3, 4, 5])
    sync = AsyncMock(
        side_effect=[
            VectorSyncBatchResult(
                entities_total=5,
                entities_synced=1,
                synced_entity_ids=(1,),
                entities_skipped=1,
                entities_failed=1,
                failed_entity_ids=(2,),
                entities_deferred=2,
                deferred_entity_ids=(3, 4),
                chunks_total=1200,
                sample_errors=("first error",),
            ),
            VectorSyncBatchResult(
                entities_total=2,
                entities_synced=0,
                entities_failed=1,
                failed_entity_ids=(3,),
                entities_deferred=1,
                deferred_entity_ids=(4,),
                chunks_total=1100,
                chunks_skipped=600,
                sample_errors=("later error", "first error", "third error"),
            ),
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=1,
                synced_entity_ids=(4,),
                entities_failed=0,
                sample_errors=("fourth error",),
            ),
        ]
    )
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync)
    progress: list[tuple[int, int, int]] = []
    stats = await search_service.reindex_vectors(lambda *event: progress.append(event))
    assert [call.args[0] for call in sync.await_args_list] == [[1, 2, 3, 4, 5], [3, 4], [4]]
    assert stats["total_entities"] == 5
    assert stats["embedded"] == stats["errors"] == 2
    assert stats["skipped"] == 1
    assert stats["deferred"] == 0
    assert stats["sample_errors"] == ("first error", "later error", "third error")
    assert progress == [(1, 1, 5), (5, 2, 5), (4, 3, 5)]
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_preserves_outcomes_across_cleanup_and_opt_out(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown IDs clean up normally; opted-out IDs never enter deferred retries."""
    monkeypatch.setattr(
        search_service.repository, "semantic_effectively_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        search_service.entity_repository,
        "find_by_ids",
        AsyncMock(
            return_value=[
                SimpleNamespace(id=2, entity_metadata={"embed": False}),
                *(SimpleNamespace(id=entity_id, entity_metadata={}) for entity_id in [3, 4, 5]),
            ]
        ),
    )
    clear = AsyncMock()
    monkeypatch.setattr(search_service, "_clear_entity_vectors", clear)

    async def sync(entity_ids: list[int], progress_callback=None) -> VectorSyncBatchResult:
        if entity_ids == [1]:
            return VectorSyncBatchResult(
                entities_total=1,
                entities_synced=1,
                synced_entity_ids=(1,),
                entities_failed=0,
                entities_skipped=1,
            )
        assert entity_ids == [3, 4, 5]
        return VectorSyncBatchResult(
            entities_total=3,
            entities_synced=1,
            synced_entity_ids=(5,),
            entities_failed=1,
            failed_entity_ids=(4,),
            entities_deferred=1,
            deferred_entity_ids=(3,),
            chunks_total=601,
            chunks_skipped=1,
        )

    monkeypatch.setattr(search_service.repository, "sync_entity_vectors_batch", sync)
    result = await search_service.sync_entity_vectors_batch([1, 2, 3, 4, 5])
    assert result.entities_total == 5
    assert result.entities_synced == 2
    assert result.synced_entity_ids == (1, 5)
    assert result.entities_failed == result.entities_deferred == result.entities_skipped == 1
    assert result.failed_entity_ids == (4,)
    assert result.deferred_entity_ids == (3,)
    assert result.chunks_total - result.chunks_skipped == 600
    clear.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_reindex_allows_arbitrarily_many_converging_shards(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Progress is convergence, not an arbitrary maximum number of passes."""
    configure_reindex(search_service, monkeypatch, [1])
    results = [
        VectorSyncBatchResult(
            entities_total=1,
            entities_synced=0,
            entities_failed=0,
            entities_deferred=1,
            deferred_entity_ids=(1,),
            chunks_total=600,
            chunks_skipped=600 - pending,
        )
        for pending in range(600, 0, -1)
    ]
    results.append(
        VectorSyncBatchResult(
            entities_total=1, entities_synced=1, synced_entity_ids=(1,), entities_failed=0
        )
    )
    sync = AsyncMock(side_effect=results)
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync)
    progress: list[tuple[int, int, int]] = []
    stats = await search_service.reindex_vectors(lambda *event: progress.append(event))
    assert stats["embedded"] == 1
    assert stats["errors"] == stats["deferred"] == 0
    assert progress == [(1, 1, 1)]
    assert sync.await_count == 601


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_chunks", [0, 600])
async def test_reindex_stops_without_persisted_progress(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch, pending_chunks: int
) -> None:
    reconcile = configure_reindex(search_service, monkeypatch, [1])
    result = VectorSyncBatchResult(
        entities_total=1,
        entities_synced=0,
        entities_failed=0,
        entities_deferred=1,
        deferred_entity_ids=(1,),
        chunks_total=pending_chunks,
        embedding_jobs_total=256,
    )
    sync = AsyncMock(return_value=result)
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync)
    progress: list[tuple[int, int, int]] = []
    stats = await search_service.reindex_vectors(lambda *event: progress.append(event))
    assert stats["deferred"] == 1
    assert stats["embedded"] == stats["errors"] == 0
    assert "not converging" in stats["sample_errors"][0]
    assert sync.await_count == (1 if pending_chunks == 0 else 2)
    assert progress == []
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_reindex_propagates_cancellation_between_shards(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = configure_reindex(search_service, monkeypatch, [1])
    sync = AsyncMock(
        side_effect=[
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=0,
                entities_failed=0,
                entities_deferred=1,
                deferred_entity_ids=(1,),
                chunks_total=600,
            ),
            asyncio.CancelledError(),
        ]
    )
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync)
    with pytest.raises(asyncio.CancelledError):
        await search_service.reindex_vectors()
    assert sync.await_count == 2
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_semantic_unavailable_reports_no_deferred_work(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_reindex(search_service, monkeypatch, [1])
    monkeypatch.setattr(
        search_service.repository, "semantic_effectively_enabled", AsyncMock(return_value=False)
    )
    stats = await search_service.reindex_vectors(force_full=True)
    assert stats["total_entities"] == stats["skipped"] == 1
    assert stats["deferred"] == stats["errors"] == stats["embedded"] == 0


@pytest.mark.asyncio
async def test_reindex_rejects_inconsistent_entity_outcomes(
    search_service: SearchService, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_reindex(search_service, monkeypatch, [1])
    monkeypatch.setattr(
        search_service,
        "sync_entity_vectors_batch",
        AsyncMock(
            return_value=VectorSyncBatchResult(
                entities_total=1, entities_synced=0, entities_failed=0, entities_deferred=1
            )
        ),
    )
    with pytest.raises(ValueError, match="counts and entity outcomes"):
        await search_service.reindex_vectors()
