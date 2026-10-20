"""Read-only integrity diagnostics against the real search tables."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text, update

from basic_memory import db
from basic_memory.models import Entity, Observation, Project, Relation
from basic_memory.repository.index_integrity import inspect_index_integrity


async def insert_index_row(session, project_id, row_id, entity_id, kind="entity"):
    await session.execute(
        text(
            "INSERT INTO search_index (project_id, id, entity_id, type) "
            "VALUES (:project, :id, :entity, :kind)"
        ),
        {"project": project_id, "id": row_id, "entity": entity_id, "kind": kind},
    )


@pytest.mark.asyncio
async def test_empty_project(session_maker, test_project):
    report = await inspect_index_integrity(session_maker, test_project.id)
    assert report.entities == report.search_rows == 0
    assert report.duplicate_keys == report.orphaned_rows == report.inverted_timestamps == 0
    assert report.missing_entity_rows == 0
    assert report.duplicate_examples == report.orphan_examples == report.missing_examples == ()


@pytest.mark.asyncio
async def test_reports_orphans_timestamps_and_does_not_repair(session_maker, sample_entity):
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            update(Entity)
            .where(Entity.id == sample_entity.id)
            .values(created_at=now, updated_at=now - timedelta(days=1))
        )
        await insert_index_row(
            session, sample_entity.project_id, sample_entity.id, sample_entity.id
        )
        await insert_index_row(session, sample_entity.project_id, 9991, 9991)
        await insert_index_row(
            session, sample_entity.project_id, 9992, sample_entity.id, "observation"
        )
        await insert_index_row(
            session, sample_entity.project_id, 9993, sample_entity.id, "relation"
        )
        # Correct entity ID with the wrong owner must also be reported.
        await insert_index_row(
            session, sample_entity.project_id, sample_entity.id + 1, sample_entity.id
        )

    report = await inspect_index_integrity(session_maker, sample_entity.project_id)
    assert (report.entities, report.search_rows, report.entity_rows) == (1, 5, 3)
    assert report.observation_rows == report.relation_rows == 1
    assert report.orphaned_rows == 4
    assert report.inverted_timestamps == 1
    assert len(report.orphan_examples) == 4
    assert report.duplicate_examples == ()
    assert await inspect_index_integrity(session_maker, sample_entity.project_id) == report


@pytest.mark.asyncio
async def test_sqlite_duplicate_counts_and_bounded_examples(
    session_maker, sample_entity, db_backend
):
    if db_backend != "sqlite":
        pytest.skip("PostgreSQL prevents duplicate primary keys")
    async with db.scoped_session(session_maker) as session:
        for _ in range(3):
            await insert_index_row(
                session, sample_entity.project_id, sample_entity.id, sample_entity.id
            )
    report = await inspect_index_integrity(session_maker, sample_entity.project_id, sample_limit=1)
    assert report.duplicate_keys == 1
    assert report.duplicate_rows == 2
    assert report.orphaned_rows == 0
    assert report.duplicate_examples == (f"entity id={sample_entity.id}: 3 copies",)


@pytest.mark.asyncio
async def test_healthy_observations_and_unresolved_relations(session_maker, sample_entity):
    async with db.scoped_session(session_maker) as session:
        observation = Observation(
            project_id=sample_entity.project_id, entity_id=sample_entity.id, content="test"
        )
        relation = Relation(
            project_id=sample_entity.project_id,
            from_id=sample_entity.id,
            to_id=None,
            to_name="Not created yet",
            relation_type="links",
        )
        session.add_all([observation, relation])
        await session.flush()
        for row_id, kind in [
            (sample_entity.id, "entity"),
            (observation.id, "observation"),
            (relation.id, "relation"),
        ]:
            await insert_index_row(
                session, sample_entity.project_id, row_id, sample_entity.id, kind
            )
    report = await inspect_index_integrity(session_maker, sample_entity.project_id)
    assert report.search_rows > report.entities
    assert report.observation_rows > 0
    assert report.relation_rows > 0
    assert report.orphaned_rows == report.duplicate_keys == report.inverted_timestamps == 0


@pytest.mark.asyncio
async def test_project_isolation_and_equal_timestamps(session_maker, sample_entity):
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            update(Entity)
            .where(Entity.id == sample_entity.id)
            .values(created_at=now, updated_at=now.astimezone(timezone(timedelta(hours=8))))
        )
        await insert_index_row(session, sample_entity.project_id, 999, 999)
    other = await inspect_index_integrity(session_maker, sample_entity.project_id + 100)
    assert other.entities == other.search_rows == other.orphaned_rows == 0
    assert (
        await inspect_index_integrity(session_maker, sample_entity.project_id)
    ).inverted_timestamps == 0


@pytest.mark.asyncio
async def test_inverted_timestamps_are_informational_not_anomalies(session_maker, sample_entity):
    """File-time fallbacks can legitimately put updated_at before created_at."""
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            update(Entity)
            .where(Entity.id == sample_entity.id)
            .values(created_at=now, updated_at=now - timedelta(days=365))
        )
        await insert_index_row(
            session, sample_entity.project_id, sample_entity.id, sample_entity.id
        )
    report = await inspect_index_integrity(session_maker, sample_entity.project_id)
    assert report.inverted_timestamps == 1
    assert report.entity_rows == report.entities == 1
    assert report.missing_entity_rows == report.orphaned_rows == report.duplicate_keys == 0


@pytest.mark.asyncio
async def test_entity_without_its_search_row_is_reported_missing(session_maker, sample_entity):
    report = await inspect_index_integrity(session_maker, sample_entity.project_id)
    assert report.entities == 1
    assert report.entity_rows == 0
    assert report.missing_entity_rows == 1
    assert report.missing_examples == (sample_entity.file_path,)


@pytest.mark.asyncio
async def test_duplicate_entity_row_cannot_hide_a_missing_one(
    session_maker, sample_entity, db_backend
):
    """Counting entities minus entity rows would call this project complete."""
    if db_backend != "sqlite":
        pytest.skip("PostgreSQL prevents duplicate primary keys")
    async with db.scoped_session(session_maker) as session:
        missing = Entity(
            project_id=sample_entity.project_id,
            title="Missing",
            note_type="note",
            content_type="text/markdown",
            file_path="missing.md",
        )
        session.add(missing)
        for _ in range(2):
            await insert_index_row(
                session, sample_entity.project_id, sample_entity.id, sample_entity.id
            )
    report = await inspect_index_integrity(session_maker, sample_entity.project_id)
    assert report.entity_rows == report.entities == 2
    assert report.missing_entity_rows == 1
    assert report.missing_examples == ("missing.md",)


@pytest.mark.asyncio
async def test_missing_index_is_an_error(session_maker, test_project, db_backend):
    async with db.scoped_session(session_maker) as session:
        cascade = " CASCADE" if db_backend == "postgres" else ""
        await session.execute(text(f"DROP TABLE search_index{cascade}"))
    with pytest.raises(Exception, match="search_index"):
        await inspect_index_integrity(session_maker, test_project.id)


@pytest.mark.asyncio
async def test_invalid_sample_limit(session_maker, test_project):
    with pytest.raises(ValueError, match="sample_limit"):
        await inspect_index_integrity(session_maker, test_project.id, sample_limit=-1)


@pytest.mark.asyncio
async def test_null_keys_and_unknown_kinds_are_orphans(session_maker, sample_entity, db_backend):
    if db_backend != "sqlite":
        pytest.skip("PostgreSQL prevents null primary keys")
    async with db.scoped_session(session_maker) as session:
        await insert_index_row(session, sample_entity.project_id, None, sample_entity.id)
        await insert_index_row(session, sample_entity.project_id, 99, sample_entity.id, "unknown")
    report = await inspect_index_integrity(session_maker, sample_entity.project_id)
    assert report.orphaned_rows == 2


@pytest.mark.asyncio
async def test_children_must_match_owner_and_project(session_maker, sample_entity):
    async with db.scoped_session(session_maker) as session:
        other_project = Project(name="other", path="/other")
        owner = Entity(
            project_id=sample_entity.project_id,
            title="Other",
            note_type="note",
            content_type="text/markdown",
            file_path="other.md",
        )
        session.add_all([other_project, owner])
        await session.flush()
        observation = Observation(
            project_id=sample_entity.project_id, entity_id=sample_entity.id, content="test"
        )
        relation = Relation(
            project_id=sample_entity.project_id,
            from_id=sample_entity.id,
            to_name="Unresolved",
            relation_type="links",
        )
        foreign = Observation(
            project_id=other_project.id, entity_id=sample_entity.id, content="foreign"
        )
        session.add_all([observation, relation, foreign])
        await session.flush()
        await insert_index_row(
            session, sample_entity.project_id, observation.id, owner.id, "observation"
        )
        await insert_index_row(session, sample_entity.project_id, relation.id, owner.id, "relation")
        await insert_index_row(
            session, sample_entity.project_id, foreign.id, sample_entity.id, "observation"
        )
        await insert_index_row(session, other_project.id, sample_entity.id, sample_entity.id)
    report = await inspect_index_integrity(session_maker, sample_entity.project_id, sample_limit=0)
    assert report.orphaned_rows == 3
    assert report.orphan_examples == ()
    other = await inspect_index_integrity(session_maker, other_project.id)
    assert other.orphaned_rows == 1


@pytest.mark.asyncio
async def test_reindexing_a_note_clears_its_duplicates_whatever_their_permalink(
    session_maker, full_entity, search_service, db_backend
):
    """Doctor's repair for duplicates is a full search reindex; this pins that it works.

    Re-indexing a note deletes all of its search rows by entity_id before writing
    them again, so a duplicate is removed even when its permalink is stale or null.
    """
    if db_backend != "sqlite":
        pytest.skip("PostgreSQL prevents duplicate primary keys")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_index (project_id, id, entity_id, type, permalink) "
                "VALUES (:project, :id, :id, 'entity', 'stale/permalink')"
            ),
            {"project": full_entity.project_id, "id": full_entity.id},
        )
        await insert_index_row(session, full_entity.project_id, full_entity.id, full_entity.id)
    before = await inspect_index_integrity(session_maker, full_entity.project_id)
    assert before.duplicate_keys == 1

    await search_service.index_entity(full_entity)

    after = await inspect_index_integrity(session_maker, full_entity.project_id)
    assert after.duplicate_keys == after.duplicate_rows == 0
    assert after.entity_rows == 1
