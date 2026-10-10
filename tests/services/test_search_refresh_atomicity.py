"""An entity's search refresh replaces its projection atomically (#1621).

The refresh used to commit the delete of the old rows on its own, so a failure while
writing the replacement left an existing note with no search rows at all.
"""

import pytest
from asyncpg.exceptions import DeadlockDetectedError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.schemas.search import SearchQuery


class ReplacementFailed(RuntimeError):
    """Stands in for the asyncpg timeout seen while writing replacement rows."""


async def _reload(session_maker, entity_repository, entity: Entity) -> Entity:
    async with db.scoped_session(session_maker) as session:
        reloaded = await entity_repository.find_by_id(session, entity.id)
    assert reloaded is not None
    return reloaded


async def _projection(search_service, entity: Entity) -> list[tuple[str, int, str | None]]:
    rows = await search_service.repository.get_entity_search_rows(entity.id)
    return sorted((row.type, row.id, row.content_snippet) for row in rows)


async def _fts_chunk_count(session_maker, entity: Entity) -> int:
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM search_index_fts_chunks c "
                "JOIN search_index s ON s.id = c.search_index_id "
                "AND s.type = c.search_index_type AND s.project_id = c.project_id "
                "WHERE s.entity_id = :entity_id"
            ),
            {"entity_id": entity.id},
        )
        return int(result.scalar_one())


def _fail_after_replacement_rows(monkeypatch, repository) -> None:
    """Write the replacement rows for real, then fail before the transaction commits."""
    original = repository.bulk_index_items

    async def write_then_fail(search_index_rows, session=None):
        await original(search_index_rows, session)
        raise ReplacementFailed("replacement write timed out")

    monkeypatch.setattr(repository, "bulk_index_items", write_then_fail)


@pytest.mark.asyncio
async def test_failed_refresh_keeps_previous_projection(
    monkeypatch, search_service, full_entity, entity_repository, session_maker
):
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="original searchable body")
    before = await _projection(search_service, entity)
    assert [kind for kind, _, _ in before].count("observation") == 2
    assert [kind for kind, _, _ in before].count("relation") == 2

    _fail_after_replacement_rows(monkeypatch, search_service.repository)
    with pytest.raises(ReplacementFailed):
        await search_service.index_entity_data(entity, content="replacement body")

    assert await _projection(search_service, entity) == before
    hits = await search_service.search(SearchQuery(text="original searchable"))
    assert entity.id in {hit.entity_id for hit in hits}


@pytest.mark.asyncio
async def test_first_index_failure_leaves_no_partial_projection(
    monkeypatch, search_service, full_entity, entity_repository, session_maker
):
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.repository.delete_by_entity_id(entity.id)
    assert await _projection(search_service, entity) == []

    _fail_after_replacement_rows(monkeypatch, search_service.repository)
    with pytest.raises(ReplacementFailed):
        await search_service.index_entity_data(entity, content="first body")
    assert await _projection(search_service, entity) == []

    monkeypatch.undo()
    await search_service.index_entity_data(entity, content="first body")
    assert len(await _projection(search_service, entity)) == 5


@pytest.mark.asyncio
async def test_successful_refresh_replaces_rows_exactly_once(
    search_service, full_entity, entity_repository, observation_repository, session_maker
):
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="original body")

    dropped = entity.observations[0]
    async with db.scoped_session(session_maker) as session:
        await observation_repository.delete(session, dropped.id)
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="replacement body")

    after = await _projection(search_service, entity)
    assert ("observation", dropped.id) not in {(kind, row_id) for kind, row_id, _ in after}
    assert sorted(kind for kind, _, _ in after) == [
        "entity",
        "observation",
        "relation",
        "relation",
    ]
    assert [snippet for kind, _, snippet in after if kind == "entity"] == ["replacement body"]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_fts_chunk_failure_keeps_previous_rows_and_chunks(
    monkeypatch, db_backend, search_service, full_entity, entity_repository, session_maker
):
    if db_backend != "postgres":
        pytest.skip("search_index_fts_chunks exists only on PostgreSQL")
    repository = search_service.repository
    assert isinstance(repository, PostgresSearchRepository)

    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="original chunked body")
    before_rows = await _projection(search_service, entity)
    before_chunks = await _fts_chunk_count(session_maker, entity)
    assert before_chunks > 0

    async def chunks_time_out(session, search_index_rows):
        raise ReplacementFailed("canceling statement due to statement timeout")

    monkeypatch.setattr(repository, "_replace_fts_chunks", chunks_time_out)
    with pytest.raises(ReplacementFailed):
        await search_service.index_entity_data(entity, content="replacement body")

    assert await _projection(search_service, entity) == before_rows
    assert await _fts_chunk_count(session_maker, entity) == before_chunks

    monkeypatch.undo()
    await search_service.index_entity_data(entity, content="replacement body")
    assert await _fts_chunk_count(session_maker, entity) == before_chunks


def _driver_failure(driver_error: BaseException) -> IntegrityError:
    """A DBAPI error raised from the driver's error, as the asyncpg dialect raises it."""
    adapted = Exception(str(driver_error))
    adapted.__cause__ = driver_error
    return IntegrityError("INSERT INTO search_index ...", {}, adapted)


def _fail_replacement_with(monkeypatch, repository, error: BaseException) -> None:
    async def fail(search_index_rows, session=None):
        raise error

    monkeypatch.setattr(repository, "bulk_index_items", fail)


@pytest.mark.asyncio
async def test_a_refresh_that_lost_a_race_returns_and_keeps_the_previous_projection(
    monkeypatch, search_service, full_entity, entity_repository, session_maker
):
    """Losing to a concurrent refresh is logged, not raised (#1681)."""
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="original body")
    before = await _projection(search_service, entity)

    _fail_replacement_with(
        monkeypatch,
        search_service.repository,
        _driver_failure(DeadlockDetectedError("deadlock detected")),
    )
    await search_service.index_entity_data(entity, content="replacement body")

    assert await _projection(search_service, entity) == before


@pytest.mark.asyncio
async def test_any_other_database_failure_still_raises(
    monkeypatch, search_service, full_entity, entity_repository, session_maker
):
    entity = await _reload(session_maker, entity_repository, full_entity)
    await search_service.index_entity_data(entity, content="original body")
    before = await _projection(search_service, entity)

    failure = _driver_failure(ValueError("value too long for type character varying"))
    _fail_replacement_with(monkeypatch, search_service.repository, failure)
    with pytest.raises(IntegrityError):
        await search_service.index_entity_data(entity, content="replacement body")

    assert await _projection(search_service, entity) == before
