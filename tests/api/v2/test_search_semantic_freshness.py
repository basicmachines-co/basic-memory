"""Search responses say how current semantic results are, on both search routes.

The note is written, full-text indexed and embedded through the real services; the
lagging note is made the way a held vector pass leaves one, with its checksum moved on
and no new embedding.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.repository import semantic_index_freshness as freshness_module


async def _embedded_note(test_project, entity_repository, search_service, file_service) -> Entity:
    file_path = Path(test_project.path) / "notes" / "Ledger.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    await file_service.write_file(file_path, "# Ledger\n\nQuarterly ledger notes")
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "title": "Ledger",
                "note_type": "note",
                "content_type": "text/markdown",
                "file_path": "notes/Ledger.md",
                "checksum": "ledger-v1",
            },
        )
    await search_service.index_entity(entity)
    assert (await search_service.sync_entity_vectors(entity.id)).entities_synced == 1
    return entity


async def _hold_embedding(session_maker, entity: Entity) -> datetime:
    """Edit the note's content with its vector pass held back; return the edit time."""
    edited_at = (datetime.now() - timedelta(hours=2)).astimezone().replace(microsecond=0)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE entity SET checksum = 'ledger-v2', updated_at = :at WHERE id = :id"),
            {"at": edited_at, "id": entity.id},
        )
    return edited_at


@pytest.mark.asyncio
async def test_project_search_reports_semantic_freshness(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
    session_maker,
):
    entity = await _embedded_note(test_project, entity_repository, search_service, file_service)

    current = await client.request("QUERY", f"{v2_project_url}/search/", json={"text": "ledger"})
    assert current.status_code == 200
    assert current.json()["semantic_index_current_as_of"] is None

    edited_at = await _hold_embedding(session_maker, entity)
    behind = await client.request("QUERY", f"{v2_project_url}/search/", json={"text": "ledger"})

    assert behind.status_code == 200
    assert datetime.fromisoformat(behind.json()["semantic_index_current_as_of"]) == edited_at
    # Full-text search still finds the held note; only its vectors lag.
    assert [row["title"] for row in behind.json()["results"]] == ["Ledger"]


@pytest.mark.asyncio
async def test_project_search_without_semantic_search_reports_nothing(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository,
    search_service,
    file_service,
    session_maker,
    monkeypatch,
):
    entity = await _embedded_note(test_project, entity_repository, search_service, file_service)
    await _hold_embedding(session_maker, entity)
    monkeypatch.setattr(freshness_module, "semantic_runtime_available", lambda _config: False)

    response = await client.request(
        "QUERY", f"{v2_project_url}/search/", json={"text": "ledger", "retrieval_mode": "fts"}
    )

    assert response.status_code == 200
    assert response.json()["semantic_index_current_as_of"] is None


@pytest.mark.asyncio
async def test_scoped_search_reports_semantic_freshness(
    client: AsyncClient,
    test_project: Project,
    entity_repository,
    search_service,
    file_service,
    session_maker,
):
    entity = await _embedded_note(test_project, entity_repository, search_service, file_service)
    body = {"project_ids": [test_project.id], "text": "ledger"}

    current = await client.request("QUERY", "/v2/search/", json=body)
    assert current.status_code == 200
    assert current.json()["semantic_index_current_as_of"] is None

    edited_at = await _hold_embedding(session_maker, entity)
    behind = await client.request("QUERY", "/v2/search/", json=body)

    assert behind.status_code == 200
    assert datetime.fromisoformat(behind.json()["semantic_index_current_as_of"]) == edited_at
