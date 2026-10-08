"""A save's outcome does not depend on its derived search refreshes (#1681).

One save runs several refreshes of the note's search rows: the freshen reindex before
the accept, the hot search row after it commits, and the materialization index of the
written file. Two saves close together overlap those refreshes, and on Postgres the one
that loses the race used to fail with ``deadlock detected`` or ``search_index_pkey`` and
turn the save into a 500. The canonical write must not depend on that derived refresh.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.schemas.v2 import EntityResponseV2
from basic_memory.services import note_content_writes

pytestmark = pytest.mark.asyncio

CONCURRENT_SAVES = 6


def _long_note(version: int) -> str:
    body = "\n\n".join(
        f"version {version} paragraph {paragraph} " + "lorem ipsum dolor sit amet " * 40
        for paragraph in range(60)
    )
    links = "\n".join(f"- relates_to [[Overlap Target {n}]]" for n in range(6))
    facts = "\n".join(f"- [fact] version {version} fact {n}" for n in range(6))
    return f"versionmarker{version}\n\n{body}\n\n{facts}\n{links}\n"


@pytest.mark.postgres
async def test_overlapping_saves_of_a_long_note_all_answer_202(
    monkeypatch, db_backend, client: AsyncClient, v2_project_url: str
):
    if db_backend != "postgres":
        pytest.skip("the collision is Postgres row locking; SQLite allows one writer")

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Overlap Note", "directory": "overlap", "content": _long_note(0)},
    )
    assert response.status_code == 202
    external_id = EntityResponseV2.model_validate(response.json()).external_id

    # Pause every index refresh between its delete and its insert, so the refreshes of
    # overlapping saves are certain to collide rather than merely likely to.
    original_bulk_index_items = PostgresSearchRepository.bulk_index_items

    async def pause_then_insert(self, search_index_rows, session=None):
        await asyncio.sleep(0.05)
        await original_bulk_index_items(self, search_index_rows, session)

    monkeypatch.setattr(PostgresSearchRepository, "bulk_index_items", pause_then_insert)

    for round_start in (1, 100, 200):
        responses = await asyncio.gather(
            *(
                client.put(
                    f"{v2_project_url}/knowledge/entities/{external_id}",
                    json={
                        "title": "Overlap Note",
                        "directory": "overlap",
                        "content": _long_note(version),
                    },
                )
                for version in range(round_start, round_start + CONCURRENT_SAVES)
            )
        )
        assert [r.status_code for r in responses] == [202] * CONCURRENT_SAVES


async def test_a_failed_post_commit_search_refresh_does_not_fail_the_save(
    monkeypatch, client: AsyncClient, v2_project_url: str, file_service
):
    """The hot search row is written after the accept commits; its failure is logged."""
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Refresh Fails", "directory": "overlap", "content": "first version"},
    )
    assert response.status_code == 202
    external_id = EntityResponseV2.model_validate(response.json()).external_id

    async def refresh_fails(*args, **kwargs):
        raise RuntimeError("search refresh failed")

    monkeypatch.setattr(note_content_writes, "refresh_accepted_note_search_index", refresh_fails)

    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json={"title": "Refresh Fails", "directory": "overlap", "content": "second version"},
    )

    assert response.status_code == 202
    saved = EntityResponseV2.model_validate(response.json())
    assert saved.db_version == 2
    assert saved.file_write_status == "synced"
    file_content, _ = await file_service.read_file(file_service.get_entity_path(saved))
    assert "second version" in file_content
