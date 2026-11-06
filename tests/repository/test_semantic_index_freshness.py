"""How current semantic search is, against a real database and real vector sync.

Notes are created, full-text indexed and embedded through the real search service
(with the deterministic test embedder). A note whose embeddings lag its content is
then made the way production makes one: its checksum moves on and no vector pass runs,
which is what a Cloud fair-use deferral leaves behind.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import sqlite

from basic_memory import db
from basic_memory.config import DatabaseBackend
from basic_memory.models import Entity, Project
from basic_memory.repository import semantic_index_freshness as freshness_module
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_index_freshness import (
    oldest_behind_statement,
    semantic_index_current_as_of,
)


def _local_time(days_ago: int) -> datetime:
    # Local-aware, because SQLite stores naive local times and reads them back as such.
    return (datetime.now() - timedelta(days=days_ago)).astimezone().replace(microsecond=0)


async def _write_file(project: Project, title: str) -> None:
    """Search indexing reads the note's file, so each note gets one."""
    path = Path(project.path) / "notes" / f"{title}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\nNotes about {title}.", encoding="utf-8")


async def _embedded_note(
    entity_repository,
    search_service,
    session_maker,
    project: Project,
    *,
    title: str,
    updated_at: datetime,
) -> Entity:
    """Create a note, index its text, and run the real vector sync over it."""
    await _write_file(project, title)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": title,
                "note_type": "note",
                "permalink": f"notes/{title.lower().replace(' ', '-')}",
                "file_path": f"notes/{title}.md",
                "content_type": "text/markdown",
                "checksum": f"{title}-v1",
                "created_at": updated_at,
                "updated_at": updated_at,
            },
        )
    await search_service.index_entity(entity)
    result = await search_service.sync_entity_vectors(entity.id)
    assert result.entities_synced == 1
    return entity


async def _edit_without_embedding(session_maker, entity: Entity, updated_at: datetime) -> None:
    """Change a note's content the way a write does, with its vector pass held back."""
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE entity SET checksum = :checksum, updated_at = :updated_at WHERE id = :id"),
            {"checksum": f"{entity.title}-v2", "updated_at": updated_at, "id": entity.id},
        )


async def _current_as_of(session_maker, app_config, test_project) -> datetime | None:
    return await semantic_index_current_as_of(
        session_maker, app_config, ProjectScope.single(test_project.id)
    )


@pytest.mark.asyncio
async def test_all_embeddings_current_is_none(
    entity_repository, search_service, session_maker, app_config, test_project
):
    entity = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Current",
        updated_at=_local_time(3),
    )

    async with db.scoped_session(session_maker) as session:
        stored = await session.get(Entity, entity.id)
    assert stored is not None
    assert stored.embedded_checksum == stored.checksum
    assert await _current_as_of(session_maker, app_config, test_project) is None


@pytest.mark.asyncio
async def test_notes_behind_report_the_latest_embedded_change(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """The newest note that is current is the answer; the notes behind only switch it on."""
    newest_current_time = _local_time(10)
    await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Old Current",
        updated_at=newest_current_time,
    )
    held = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Held",
        updated_at=_local_time(5),
    )
    newer_held = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Newer Held",
        updated_at=_local_time(4),
    )
    await _edit_without_embedding(session_maker, held, _local_time(2))
    await _edit_without_embedding(session_maker, newer_held, _local_time(1))

    assert await _current_as_of(session_maker, app_config, test_project) == newest_current_time

    # The held pass runs: those notes catch up and search is current again.
    await search_service.sync_entity_vectors(held.id)
    await search_service.sync_entity_vectors(newer_held.id)
    assert await _current_as_of(session_maker, app_config, test_project) is None


@pytest.mark.asyncio
async def test_one_stuck_note_does_not_date_the_index_back(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """A note behind for weeks must not make every search claim weeks-old vectors."""
    stuck = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Stuck",
        updated_at=_local_time(60),
    )
    await _edit_without_embedding(session_maker, stuck, _local_time(59))
    recent_time = _local_time(1)
    await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Recent",
        updated_at=recent_time,
    )

    assert await _current_as_of(session_maker, app_config, test_project) == recent_time


@pytest.mark.asyncio
async def test_a_note_never_embedded_is_behind(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """A new note whose first vector pass has not run is not in semantic search yet.

    With nothing embedded there is no embedded change to report, so the answer is the
    oldest note behind.
    """
    created_at = _local_time(1)
    await _write_file(test_project, "Fresh")
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": "Fresh",
                "note_type": "note",
                "permalink": "notes/fresh",
                "file_path": "notes/Fresh.md",
                "content_type": "text/markdown",
                "checksum": "fresh-v1",
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
    await search_service.index_entity(entity)

    assert await _current_as_of(session_maker, app_config, test_project) == created_at


@pytest.mark.asyncio
async def test_a_note_left_part_way_is_behind(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """A pass that embedded one shard of a large note leaves it behind (#1440)."""
    updated_at = _local_time(2)
    entity = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Sharded",
        updated_at=updated_at,
    )
    await search_service.repository.record_entity_vector_deferrals(
        unfinished_entity_ids={entity.id},
        completed_entity_ids=set(),
        pass_started_at=datetime.now().astimezone(),
    )

    assert await _current_as_of(session_maker, app_config, test_project) == updated_at


@pytest.mark.asyncio
async def test_an_opted_out_note_owes_nothing(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """`embed: false` notes are never embedded, so they never hold search back."""
    updated_at = _local_time(2)
    await _write_file(test_project, "Private")
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": "Private",
                "note_type": "note",
                "permalink": "notes/private",
                "file_path": "notes/Private.md",
                "content_type": "text/markdown",
                "checksum": "private-v1",
                "entity_metadata": {"embed": False},
                "created_at": updated_at,
                "updated_at": updated_at,
            },
        )
    await search_service.index_entity(entity)
    assert await _current_as_of(session_maker, app_config, test_project) == updated_at

    await search_service.sync_entity_vectors(entity.id)

    assert await _current_as_of(session_maker, app_config, test_project) is None


@pytest.mark.asyncio
async def test_an_embedded_checksum_older_than_the_note_stays_behind(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """A write during a pass leaves the stamp on the checksum the pass started from."""
    entity = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Racing",
        updated_at=_local_time(3),
    )
    edit_time = _local_time(1)
    await _edit_without_embedding(session_maker, entity, edit_time)
    # What the pass that started before the edit stamps when it finishes.
    await search_service.repository.record_entity_embedded_checksums({entity.id: "Racing-v1"})

    assert await _current_as_of(session_maker, app_config, test_project) == edit_time


@pytest.mark.asyncio
async def test_non_markdown_files_are_ignored(
    entity_repository, session_maker, app_config, test_project
):
    """Attachments are never embedded, so they never hold search back."""
    async with db.scoped_session(session_maker) as session:
        await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": "image.png",
                "note_type": "file",
                "file_path": "files/image.png",
                "content_type": "image/png",
                "checksum": "png",
                "created_at": _local_time(1),
                "updated_at": _local_time(1),
            },
        )

    assert await _current_as_of(session_maker, app_config, test_project) is None


@pytest.mark.asyncio
async def test_semantic_search_unavailable_is_none(
    entity_repository, search_service, session_maker, app_config, test_project, monkeypatch
):
    """A keyword-only host (#711) has no semantic results to be behind."""
    entity = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Keyword",
        updated_at=_local_time(3),
    )
    await _edit_without_embedding(session_maker, entity, _local_time(1))
    monkeypatch.setattr(freshness_module, "semantic_runtime_available", lambda _config: False)

    assert await _current_as_of(session_maker, app_config, test_project) is None


@pytest.mark.asyncio
async def test_an_empty_scope_is_none(session_maker, app_config):
    assert (
        await semantic_index_current_as_of(session_maker, app_config, ProjectScope.of([])) is None
    )


@pytest.mark.asyncio
async def test_the_lookup_reads_the_partial_index(session_maker, app_config, test_project):
    """Each search pays for this check, so it must not scan the project's entities."""
    if app_config.database_backend != DatabaseBackend.SQLITE:
        pytest.skip("Postgres picks partial indexes by cost; the planner is not asserted.")
    compiled = oldest_behind_statement(ProjectScope.single(test_project.id)).compile(
        dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
    )
    async with db.scoped_session(session_maker) as session:
        rows = (await session.execute(text(f"EXPLAIN QUERY PLAN {compiled}"))).all()
    plan = " ".join(str(row[-1]) for row in rows)

    assert "ix_entity_project_embedding_not_current" in plan


@pytest.mark.asyncio
async def test_opting_out_a_part_way_note_settles_it(
    entity_repository, search_service, session_maker, app_config, test_project
):
    """A note left part-way, then marked `embed: false`, no longer holds search back.

    Opt-out skips the repository sync boundary that clears deferral markers, so the
    marker the earlier pass left must be cleared by the opt-out itself.
    """
    updated_at = _local_time(2)
    entity = await _embedded_note(
        entity_repository,
        search_service,
        session_maker,
        test_project,
        title="Abandoned",
        updated_at=updated_at,
    )
    await search_service.repository.record_entity_vector_deferrals(
        unfinished_entity_ids={entity.id},
        completed_entity_ids=set(),
        pass_started_at=datetime.now().astimezone(),
    )
    assert await _current_as_of(session_maker, app_config, test_project) == updated_at

    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("UPDATE entity SET entity_metadata = :metadata WHERE id = :id"),
            {"metadata": '{"embed": false}', "id": entity.id},
        )
    await search_service.sync_entity_vectors(entity.id)

    assert await _current_as_of(session_maker, app_config, test_project) is None
