"""An accepted API edit embeds once, after its file is materialized (#1732).

The accept path publishes only the note body as a temporary search row; the
observation and relation rows appear when materialization indexes the written file.
A request-time embed used to race that job, embed the body alone, and leave the
observation and relation vectors missing with nothing to re-embed them.

Adapted from the reproducer @beru-ant-king attached to #1732. Integration tests
materialize inline, so these tests restore the production path: the accept enqueues
the file write on the worker pool and the request returns before it runs.
"""

import asyncio
from collections import Counter
from dataclasses import replace

import pytest

from basic_memory.index import local_schedulers
from basic_memory.index.note_content_materialization import (
    LocalNoteContentMaterializationProvider,
    drain_pending_materializations,
)
from basic_memory.repository.semantic_chunking import build_vector_chunk_records
from basic_memory.services.search_service import SearchService


async def vector_coverage(search_service: SearchService, entity_id: int) -> dict[str, object]:
    """Compare chunk keys derived from the current search rows with stored vectors."""
    repository = search_service.repository
    sources = await repository.get_entity_search_rows(entity_id)
    expected = {record["chunk_key"] for record in build_vector_chunk_records(sources).records}
    manifest = await repository.get_entity_chunk_manifest(entity_id)
    return {
        "source_types": dict(Counter(row.type for row in sources)),
        "expected": expected,
        "manifest": {row.chunk_key for row in manifest},
        "physical": await repository.get_entity_physical_chunk_keys(entity_id),
        "pending": sum(row.embedding_status != "ready" for row in manifest),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("materialization_delayed", [True, False])
async def test_api_edit_embeds_once_after_materialization(
    client, test_project, search_service, monkeypatch, materialization_delayed
):
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    target = await client.post(
        base + "/write",
        json={"note": {"title": "Target", "directory": "notes", "content": "Stable target."}},
    )
    assert target.status_code == 200, target.text
    created = await client.post(
        base + "/write",
        json={
            "note": {
                "title": "Source",
                "directory": "notes",
                "content": "# Source\n\n- [fact] Original observation\n\n- relates_to [[Target]]\n",
            }
        },
    )
    assert created.status_code == 200, created.text
    entity = created.json()["entity"]
    entity_id = entity["id"]

    # --- Restore production materialization and count vector syncs ---
    materialize_now = LocalNoteContentMaterializationProvider._materialize_write_now
    accept_materialization = LocalNoteContentMaterializationProvider.materialize_write_change
    sync_entity_vectors = SearchService.sync_entity_vectors
    schedule_background = local_schedulers._schedule_background_coroutine
    release = asyncio.Event()
    if not materialization_delayed:
        release.set()
    synced_entity_ids: list[int] = []

    async def paused_materialize(self, accepted):
        # Hold the file write until the request and any request-time background work
        # have finished: the order in which a request-time embed saw only the body.
        await release.wait()
        return await materialize_now(self, accepted)

    async def production_materialization(self, accepted):
        return await accept_materialization(replace(self, test_mode=False), accepted)

    async def counted_sync(self, synced_entity_id):
        synced_entity_ids.append(synced_entity_id)
        return await sync_entity_vectors(self, synced_entity_id)

    monkeypatch.setattr(
        LocalNoteContentMaterializationProvider, "_materialize_write_now", paused_materialize
    )
    monkeypatch.setattr(
        LocalNoteContentMaterializationProvider,
        "materialize_write_change",
        production_materialization,
    )
    monkeypatch.setattr(SearchService, "sync_entity_vectors", counted_sync)
    # Background follow-ups run for real too, so any request-time embed would race.
    monkeypatch.setattr(
        local_schedulers,
        "_schedule_background_coroutine",
        lambda coroutine, *, test_mode: schedule_background(coroutine, test_mode=False),
    )

    # --- Edit, then let every follow-up settle ---
    try:
        response = await client.patch(
            base + f"/entities/{entity['external_id']}",
            json={"operation": "append", "content": "\n- [fact] Newly accepted observation\n"},
        )
        assert response.status_code == 202, response.text
        await local_schedulers.drain_background_tasks()
        release.set()
        await drain_pending_materializations()
        await local_schedulers.drain_background_tasks()
    finally:
        release.set()
        await drain_pending_materializations()

    coverage = await vector_coverage(search_service, entity_id)
    assert coverage["source_types"] == {"entity": 1, "observation": 2, "relation": 1}
    assert coverage["expected"] == coverage["manifest"] == coverage["physical"]
    assert coverage["pending"] == 0
    assert synced_entity_ids == [entity_id]
