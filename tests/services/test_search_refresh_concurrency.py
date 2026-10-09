"""Overlapping refreshes of one entity's search rows never fail the work that asked for them.

Saving a long note twice in quick succession ran two delete-then-insert refreshes of the
same entity at once. On Postgres one of them failed with ``deadlock detected`` or with
``search_index_pkey`` on the entity row or a relation row, and the save that triggered it
answered 500 (#1681). Search rows are derived state, so the refresh that loses is logged
and left for the next refresh, and the rollback keeps the winner's whole projection.

A real 80 KB note opens the race window by itself: indexing it takes longer than the gap
between saves. Here every refresh also pauses between its delete and its insert, and the
collision rounds start from an entity whose rows another writer has just deleted, so the
deletes have nothing to wait on and the inserts arrive together. The negative controls
remove the tolerance and show the same refreshes still collide underneath, so the
positive tests are not passing by luck.
"""

from __future__ import annotations

import asyncio
import os
import random
import string
import uuid

import pytest

from basic_memory import db
from basic_memory.indexing.accepted_note_write_runner import (
    accepted_note_search_row_from_entity,
    refresh_accepted_note_search_index,
)
from basic_memory.models import Entity
from basic_memory.repository.accepted_note_repositories import AcceptedNoteRepositories
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


def _remove_the_race_tolerance(monkeypatch) -> None:
    monkeypatch.setattr(search_service_module, "lost_search_refresh_race", lambda error: None)


async def _projection(search_service, entity: Entity) -> list[tuple[str, int]]:
    rows = await search_service.repository.get_entity_search_rows(entity.id)
    return sorted((row.type, row.id) for row in rows if row.entity_id == entity.id)


async def _entity_row_content(search_service, entity: Entity) -> str:
    rows = await search_service.repository.get_entity_search_rows(entity.id)
    [entity_row] = [row for row in rows if row.type == "entity"]
    return entity_row.content_snippet


def _skip_unless_postgres(db_backend) -> None:
    if db_backend != "postgres":
        pytest.skip("the collision is Postgres row locking; SQLite allows one writer")


def _write_atomically(path, text: str) -> None:
    """Replace the file in one step, as the materializer's atomic write does.

    A plain write_text truncates first, so a concurrent refresh could read an empty or
    half-written file and index a body that starts with no version marker.
    """
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    staged.write_text(text)
    os.replace(staged, path)


async def _save_then_index(search_service, file_service, entity: Entity, version: int) -> None:
    """What a save does: write the file, then refresh the index from storage."""
    path = file_service.get_entity_path(entity)
    await asyncio.to_thread(
        _write_atomically, path, f"---\ntitle: {entity.title}\n---\n{_long_body(version)}\n"
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


async def test_concurrent_saves_of_a_long_note_all_succeed_and_converge(
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
        # relation, the same set as before the overlap. A refresh that lost rolled back
        # entirely, so the rows are one winner's, never a mix.
        assert await _projection(search_service, entity) == before
        indexed = await _entity_row_content(search_service, entity)
        assert any(
            indexed.startswith(f"versionmarker{v}\n")
            for v in range(round_start, round_start + CONCURRENT_SAVES)
        ), f"round {round_start}: indexed body starts {indexed[:60]!r}"

        # The next refresh converges the rows to the file on disk.
        await search_service.index_entity_data(entity)
        assert await _entity_row_content(
            search_service, entity
        ) == await file_service.read_entity_content(entity)


async def test_negative_control_without_the_tolerance_overlapping_saves_fail(
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
    _remove_the_race_tolerance(monkeypatch)

    failures: list[BaseException] = []
    # The collision is a race, so a round can miss; ten rounds have not all missed.
    for round_start in range(0, 1000, 100):
        results = await _save_round(search_service, file_service, entity, round_start)
        failures += [r for r in results if isinstance(r, BaseException)]
        if failures:
            break

    messages = " ".join(str(failure) for failure in failures)
    assert failures, "without the tolerance, overlapping saves were expected to collide"
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


async def test_negative_control_relation_rows_collide_without_the_tolerance(
    monkeypatch, db_backend, search_service, full_entity, entity_repository, session_maker
):
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    _widen_the_window(monkeypatch, search_service.repository)
    _remove_the_race_tolerance(monkeypatch)

    failures: list[BaseException] = []
    for _ in range(10):
        failures += await _refresh_relations_concurrently(search_service, entity)
        if failures:
            break
    assert failures, "without the tolerance, overlapping refreshes were expected to collide"
    assert "search_index_pkey" in " ".join(str(failure) for failure in failures)


async def test_an_accepted_write_and_an_index_refresh_overlap_without_failing(
    monkeypatch, db_backend, search_service, full_entity, entity_repository, session_maker
):
    """The save's own hot search refresh rewrites the entity row too; neither side fails."""
    _skip_unless_postgres(db_backend)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="before")
    _widen_the_window(monkeypatch, search_service.repository)
    repositories = AcceptedNoteRepositories()

    async def accepted_write(version: int) -> None:
        await refresh_accepted_note_search_index(
            session_maker,
            row=accepted_note_search_row_from_entity(entity, search_content=_long_body(version)),
            repositories=repositories,
        )

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
