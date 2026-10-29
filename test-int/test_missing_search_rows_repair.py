"""Entities that lost their search_index rows converge again (#1826).

Migrations that recreated search_index left unchanged notes with an entity row but
no search rows. Change detection compares checksums only, and vectors are cut from
search rows, so those notes stayed unsearchable in every mode while `project info`
reported full coverage. These tests remove one note's search projection and prove
each path that should repair or report it does.
"""

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import ConfigManager
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.project_service import ProjectService
from basic_memory.services.search_service import SearchService

NOTE_PATH = "notes/lost-rows.md"
NOTE_CONTENT = """---
title: Lost Rows
type: note
permalink: notes/lost-rows
---

# Lost Rows

Searchable text about heliotrope gardens.

- [fact] Heliotrope blooms in late summer
"""


async def _index_project(project: Project, *, embeddings: bool):
    return await run_local_project_index_for_project(
        project,
        runtime_factory=LocalProjectIndexRuntimeFactory(),
        embeddings=embeddings,
    )


async def _count(session_maker, sql: str, params: dict[str, object]) -> int:
    async with db.scoped_session(session_maker) as session:
        return int(await session.scalar(text(sql), params) or 0)


async def _indexed_note_entity_id(session_maker, project: Project) -> int:
    async with db.scoped_session(session_maker) as session:
        entity_id = await session.scalar(
            text("SELECT id FROM entity WHERE project_id = :project_id AND file_path = :path"),
            {"project_id": project.id, "path": NOTE_PATH},
        )
    assert entity_id is not None
    return int(entity_id)


async def _drop_search_projection(session_maker, project: Project, entity_id: int) -> None:
    """Leave the entity and its file in place, with no search rows and no vectors."""
    params = {"project_id": project.id, "entity_id": entity_id}
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "DELETE FROM search_index WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            params,
        )
        await session.execute(
            text(
                "DELETE FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            params,
        )


async def _search_row_count(session_maker, project: Project, entity_id: int) -> int:
    return await _count(
        session_maker,
        "SELECT COUNT(*) FROM search_index "
        "WHERE project_id = :project_id AND entity_id = :entity_id AND type = 'entity'",
        {"project_id": project.id, "entity_id": entity_id},
    )


async def _chunk_count(session_maker, project: Project, entity_id: int) -> int:
    return await _count(
        session_maker,
        "SELECT COUNT(*) FROM search_vector_chunks "
        "WHERE project_id = :project_id AND entity_id = :entity_id",
        {"project_id": project.id, "entity_id": entity_id},
    )


@pytest.mark.asyncio
async def test_index_pass_restores_unchanged_entity_missing_search_rows(
    test_project: Project,
    project_config,
    engine_factory,
    config_manager: ConfigManager,
) -> None:
    _engine, session_maker = engine_factory
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    (project_config.home / NOTE_PATH).write_text(NOTE_CONTENT, encoding="utf-8")

    await _index_project(test_project, embeddings=True)
    entity_id = await _indexed_note_entity_id(session_maker, test_project)
    assert await _search_row_count(session_maker, test_project, entity_id) == 1
    assert await _chunk_count(session_maker, test_project, entity_id) > 0

    await _drop_search_projection(session_maker, test_project, entity_id)

    # project info measures coverage against the entity table, so the gap shows.
    project_service = ProjectService(ProjectRepository(), session_maker)
    status = await project_service.get_embedding_status(test_project.id)
    assert status.entities_missing_search_rows == 1
    assert status.reindex_recommended is True
    assert status.reindex_reason == "1 entities missing from the search index — run: bm reindex"

    # The file is unchanged, so only the missing-row repair can bring it back.
    repaired = await _index_project(test_project, embeddings=True)

    assert repaired.enqueued_files == 0
    assert repaired.restored_search_entities == 1
    assert await _search_row_count(session_maker, test_project, entity_id) == 1
    assert await _chunk_count(session_maker, test_project, entity_id) > 0
    status = await project_service.get_embedding_status(test_project.id)
    assert status.entities_missing_search_rows == 0

    converged = await _index_project(test_project, embeddings=True)
    assert converged.restored_search_entities == 0


@pytest.mark.asyncio
async def test_reindex_embeddings_restores_search_rows_before_embedding(
    test_project: Project,
    project_config,
    engine_factory,
) -> None:
    _engine, session_maker = engine_factory
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    (project_config.home / NOTE_PATH).write_text(NOTE_CONTENT, encoding="utf-8")

    await _index_project(test_project, embeddings=True)
    entity_id = await _indexed_note_entity_id(session_maker, test_project)
    await _drop_search_projection(session_maker, test_project, entity_id)

    # `bm reindex --full --embeddings` runs no search pass; the vector rebuild
    # itself must restore the rows its chunks are cut from.
    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(test_project)
    search_service = dependencies.search_service
    assert isinstance(search_service, SearchService)
    stats = await search_service.reindex_vectors(force_full=True)

    assert stats["search_rows_restored"] == 1
    assert await _search_row_count(session_maker, test_project, entity_id) == 1
    assert await _chunk_count(session_maker, test_project, entity_id) > 0


@pytest.mark.asyncio
async def test_restore_missing_search_rows_loads_entities_in_bounded_batches(
    test_project: Project,
    project_config,
    engine_factory,
    monkeypatch,
) -> None:
    """A migration can strip every entity's rows; repair must not load them all at once."""
    _engine, session_maker = engine_factory
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    for index in range(5):
        (project_config.home / f"notes/batch-{index}.md").write_text(
            NOTE_CONTENT.replace("lost-rows", f"batch-{index}").replace(
                "Lost Rows", f"Batch {index}"
            ),
            encoding="utf-8",
        )
    await _index_project(test_project, embeddings=False)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("DELETE FROM search_index WHERE project_id = :project_id"),
            {"project_id": test_project.id},
        )

    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(test_project)
    search_service = dependencies.search_service
    assert isinstance(search_service, SearchService)
    entity_repository = search_service.entity_repository
    loaded_batch_sizes: list[int] = []
    find_by_ids = entity_repository.find_by_ids

    async def recording_find_by_ids(session, ids):
        loaded_batch_sizes.append(len(ids))
        return await find_by_ids(session, ids)

    monkeypatch.setattr(entity_repository, "find_by_ids", recording_find_by_ids)

    restored = await search_service.restore_missing_search_rows(batch_size=2)

    assert loaded_batch_sizes == [2, 2, 1]
    assert len(restored) == 5
    missing = await _count(
        session_maker,
        "SELECT COUNT(*) FROM entity WHERE project_id = :project_id AND id NOT IN "
        "(SELECT entity_id FROM search_index WHERE project_id = :project_id AND type = 'entity')",
        {"project_id": test_project.id},
    )
    assert missing == 0


@pytest.mark.asyncio
async def test_entity_row_under_another_id_counts_as_missing(
    test_project: Project,
    project_config,
    engine_factory,
    config_manager: ConfigManager,
) -> None:
    """A stale entity row with the right entity_id but another id is not a projection.

    Search keys entity rows by the entity's own id, as the integrity checker does, so
    the repair must treat this shape as missing and rebuild it.
    """
    _engine, session_maker = engine_factory
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    (project_config.home / NOTE_PATH).write_text(NOTE_CONTENT, encoding="utf-8")
    await _index_project(test_project, embeddings=False)
    entity_id = await _indexed_note_entity_id(session_maker, test_project)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_index SET id = :stale_id WHERE project_id = :project_id "
                "AND type = 'entity' AND entity_id = :entity_id"
            ),
            {
                "stale_id": entity_id + 100_000,
                "project_id": test_project.id,
                "entity_id": entity_id,
            },
        )

    project_service = ProjectService(ProjectRepository(), session_maker)
    status = await project_service.get_embedding_status(test_project.id)
    assert status.entities_missing_search_rows == 1

    repaired = await _index_project(test_project, embeddings=False)
    assert repaired.restored_search_entities == 1
    restored = await _count(
        session_maker,
        "SELECT COUNT(*) FROM search_index WHERE project_id = :project_id "
        "AND type = 'entity' AND id = :entity_id",
        {"project_id": test_project.id, "entity_id": entity_id},
    )
    assert restored == 1


@pytest.mark.asyncio
async def test_partially_indexed_entity_is_counted_once(
    test_project: Project,
    project_config,
    engine_factory,
    config_manager: ConfigManager,
) -> None:
    """An entity that keeps observation rows but lost its entity row counts once, as missing.

    Coverage adds indexed and missing entities, so the two counts must not overlap.
    """
    _engine, session_maker = engine_factory
    (project_config.home / "notes").mkdir(parents=True, exist_ok=True)
    (project_config.home / NOTE_PATH).write_text(NOTE_CONTENT, encoding="utf-8")
    await _index_project(test_project, embeddings=False)
    entity_id = await _indexed_note_entity_id(session_maker, test_project)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "DELETE FROM search_index WHERE project_id = :project_id "
                "AND type = 'entity' AND id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": entity_id},
        )
    observation_rows = await _count(
        session_maker,
        "SELECT COUNT(*) FROM search_index WHERE project_id = :project_id "
        "AND entity_id = :entity_id",
        {"project_id": test_project.id, "entity_id": entity_id},
    )
    assert observation_rows > 0

    project_service = ProjectService(ProjectRepository(), session_maker)
    status = await project_service.get_embedding_status(test_project.id)

    entities = await _count(
        session_maker,
        "SELECT COUNT(*) FROM entity WHERE project_id = :project_id",
        {"project_id": test_project.id},
    )
    assert status.entities_missing_search_rows == 1
    assert status.total_indexed_entities + status.entities_missing_search_rows == entities


@pytest.mark.asyncio
async def test_one_unreadable_file_does_not_stop_the_repair(
    test_project: Project,
    project_config,
    engine_factory,
    config_manager: ConfigManager,
) -> None:
    """A file that cannot be decoded is skipped; the other entities still converge."""
    _engine, session_maker = engine_factory
    notes = project_config.home / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    for name in ("bad", "good"):
        (notes / f"{name}.md").write_text(
            NOTE_CONTENT.replace("lost-rows", name).replace("Lost Rows", name.title()),
            encoding="utf-8",
        )
    await _index_project(test_project, embeddings=False)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text("DELETE FROM search_index WHERE project_id = :project_id"),
            {"project_id": test_project.id},
        )
    # Invalid UTF-8 after indexing: the read fails during the repair, not the scan.
    (notes / "bad.md").write_bytes(b"\xff\xfe not utf-8 \xc3\x28")

    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(test_project)
    search_service = dependencies.search_service
    assert isinstance(search_service, SearchService)

    restored = await search_service.restore_missing_search_rows()

    assert len(restored) == 1
    good_rows = await _count(
        session_maker,
        "SELECT COUNT(*) FROM search_index s JOIN entity e ON e.id = s.id "
        "WHERE s.project_id = :project_id AND s.type = 'entity' AND e.file_path = :path",
        {"project_id": test_project.id, "path": "notes/good.md"},
    )
    assert good_rows == 1
