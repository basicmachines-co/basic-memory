"""Overlapping refreshes of one entity's search rows all succeed.

Saving a long note twice in quick succession ran two delete-then-insert refreshes of
the same entity at once. On Postgres the second one failed with ``deadlock detected``
or with ``search_index_pkey`` on the entity row or a relation row, and the save that
triggered it answered 500. These tests run N refreshes of one long note at once.

A real 80 KB note opens the race window by itself: indexing it takes longer than the
gap between saves. Here every refresh also pauses between its delete and its insert,
and the collision rounds start from an entity whose rows another writer has just
deleted, so the deletes have nothing to wait on and the inserts arrive together. The
negative controls remove the lock and show the same refreshes fail.
"""

from __future__ import annotations

import asyncio
import random
import string

import pytest

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.accepted_note_search_row import AcceptedNoteSearchRow
from basic_memory.services import search_service as search_service_module

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]

CONCURRENT_SAVES = 6
NOTE_BYTES = 80_000


def _long_body(version: int) -> str:
    rnd = random.Random(version)
    words = [
        "".join(rnd.choice(string.ascii_lowercase) for _ in range(rnd.randint(3, 10)))
        for _ in range(NOTE_BYTES // 7)
    ]
    lines = [" ".join(words[i : i + 14]) for i in range(0, len(words), 14)]
    return f"versionmarker{version}\n\n" + "\n\n".join(lines)


async def _reload(session_maker, entity_repository, entity: Entity) -> Entity:
    async with db.scoped_session(session_maker) as session:
        reloaded = await entity_repository.find_by_id(session, entity.id)
    assert reloaded is not None
    return reloaded


def _widen_the_window(monkeypatch, repository) -> None:
    """Pause between a refresh's delete and its insert, while it holds the deleted rows."""
    original = repository.bulk_index_items

    async def pause_then_insert(search_index_rows, session=None):
        await asyncio.sleep(0.05)
        await original(search_index_rows, session)

    monkeypatch.setattr(repository, "bulk_index_items", pause_then_insert)


def _remove_the_lock(monkeypatch) -> None:
    async def no_lock(session, *, project_id, entity_id):
        return None

    monkeypatch.setattr(search_service_module, "lock_entity_search_projection", no_lock)


async def _projection(search_service, entity: Entity) -> list[tuple[str, int]]:
    rows = await search_service.repository.get_entity_search_rows(entity.id)
    return sorted((row.type, row.id) for row in rows if row.entity_id == entity.id)


def _skip_unless_postgres(db_backend) -> None:
    if db_backend != "postgres":
        pytest.skip("the collision is Postgres row locking; SQLite allows one writer")


async def _save_then_index(search_service, file_service, entity: Entity, version: int) -> None:
    """What a save does: write the file, then refresh the index from storage."""
    path = file_service.get_entity_path(entity)
    await asyncio.to_thread(
        path.write_text, f"---\ntitle: {entity.title}\n---\n{_long_body(version)}\n"
    )
    await search_service.index_entity_data(entity)


async def _save_round(search_service, file_service, entity: Entity, start: int) -> list[object]:
    """N saves at once, from an entity whose old rows another writer just deleted.

    With no rows to wait on, every refresh's delete passes at once and all of them
    reach their inserts together, which is when ``search_index_pkey`` fires.
    """
    await search_service.repository.delete_by_entity_id(entity.id)
    return await asyncio.gather(
        *(
            _save_then_index(search_service, file_service, entity, v)
            for v in range(start, start + CONCURRENT_SAVES)
        ),
        return_exceptions=True,
    )


async def test_concurrent_saves_of_a_long_note_all_succeed_and_the_last_one_wins(
    monkeypatch,
    db_backend,
    search_service,
    file_service,
    full_entity,
    entity_repository,
    session_maker,
):
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    before = await _projection(search_service, entity)
    _widen_the_window(monkeypatch, search_service.repository)

    for round_start in (0, 100, 200):
        results = await _save_round(search_service, file_service, entity, round_start)
        assert [r for r in results if isinstance(r, BaseException)] == []

        # The projection is whole and single: one row per entity, observation and
        # relation, the same set as before the overlap.
        assert await _projection(search_service, entity) == before

        # The index holds the text on disk now, which is the last save's.
        on_disk = await file_service.read_entity_content(entity)
        rows = await search_service.repository.get_entity_search_rows(entity.id)
        [entity_row] = [row for row in rows if row.type == "entity"]
        assert entity_row.content_snippet == on_disk


async def test_negative_control_without_the_lock_overlapping_saves_fail(
    monkeypatch,
    db_backend,
    search_service,
    file_service,
    full_entity,
    entity_repository,
    session_maker,
):
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    _widen_the_window(monkeypatch, search_service.repository)
    _remove_the_lock(monkeypatch)

    failures: list[BaseException] = []
    # The collision is a race, so a round can miss; ten rounds have not all missed.
    for round_start in range(0, 1000, 100):
        results = await _save_round(search_service, file_service, entity, round_start)
        failures += [r for r in results if isinstance(r, BaseException)]
        if failures:
            break

    messages = " ".join(str(failure) for failure in failures)
    assert failures, "without the lock, overlapping saves were expected to collide"
    assert "search_index_pkey" in messages or "deadlock detected" in messages


async def _refresh_relations_concurrently(search_service, entity: Entity) -> list[BaseException]:
    """Refresh with the content in hand, as the batch indexer does; relation rows ride along.

    The round starts from an entity with no rows, as after a delete another writer has
    committed. Every refresh's delete then finds nothing to wait on, and all of them
    reach their inserts together: the shape that produced ``Key (id, relation, N)``.
    """
    await search_service.repository.delete_by_entity_id(entity.id)
    results = await asyncio.gather(
        *(
            search_service.index_entity_data(entity, content=_long_body(v))
            for v in range(CONCURRENT_SAVES)
        ),
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, BaseException)]


async def test_concurrent_refreshes_keep_relation_rows_single(
    monkeypatch, db_backend, search_service, full_entity, entity_repository, session_maker
):
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    before = await _projection(search_service, entity)
    assert [kind for kind, _ in before].count("relation") == 2
    _widen_the_window(monkeypatch, search_service.repository)

    for _ in range(3):
        assert await _refresh_relations_concurrently(search_service, entity) == []
        assert await _projection(search_service, entity) == before


async def test_negative_control_relation_rows_collide_without_the_lock(
    monkeypatch, db_backend, search_service, full_entity, entity_repository, session_maker
):
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    _widen_the_window(monkeypatch, search_service.repository)
    _remove_the_lock(monkeypatch)

    failures: list[BaseException] = []
    for _ in range(10):
        failures += await _refresh_relations_concurrently(search_service, entity)
        if failures:
            break
    assert failures, "without the lock, overlapping refreshes were expected to collide"
    assert "search_index_pkey" in " ".join(str(failure) for failure in failures)


async def test_an_accepted_write_and_an_index_refresh_take_turns(
    monkeypatch, db_backend, search_service, full_entity, entity_repository, session_maker
):
    """The save path's accepted write rewrites the entity row too; it waits its turn."""
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    _widen_the_window(monkeypatch, search_service.repository)
    accepted = AcceptedNoteSearchRepository(project_id=entity.project_id)

    async def accepted_write(version: int) -> None:
        body = _long_body(version)
        async with db.scoped_session(session_maker) as session:
            await accepted.refresh_entity(
                session,
                AcceptedNoteSearchRow(
                    id=entity.id,
                    title=entity.title,
                    content_stems=body,
                    content_snippet=body,
                    permalink=entity.permalink,
                    file_path=entity.file_path,
                    item_type="entity",
                    note_type=entity.note_type,
                    entity_id=entity.id,
                    created_at=entity.created_at,
                    updated_at=entity.updated_at,
                    project_id=entity.project_id,
                ),
            )
            await asyncio.sleep(0.05)

    for round_start in (0, 100, 200):
        results = await asyncio.gather(
            *(
                accepted_write(v)
                if v % 2
                else search_service.index_entity_data(entity, content=_long_body(v))
                for v in range(round_start, round_start + CONCURRENT_SAVES)
            ),
            return_exceptions=True,
        )
        assert [r for r in results if isinstance(r, BaseException)] == []
        rows = await search_service.repository.get_entity_search_rows(entity.id)
        assert [row.type for row in rows].count("entity") == 1
