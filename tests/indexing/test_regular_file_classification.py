"""Tests for resource MIME normalization at the indexing boundary."""

from datetime import UTC, datetime

from basic_memory import db
from basic_memory.indexing.batch_indexer import (
    BatchIndexer,
    RUNTIME_RESOURCE_CONTENT_TYPE,
    regular_file_content_type,
)
from basic_memory.indexing.models import IndexInputFile, StorageIndexFileWriter
from basic_memory.models import Entity, Observation, Relation, RelationSearchRefresh
from basic_memory.repository import NoteContentRepository


def test_markdown_mime_without_note_basename_is_persisted_as_resource() -> None:
    file = IndexInputFile(
        path="_phase7_import/.md",
        content_type="text/markdown",
        content=b"poison object",
        size=13,
    )

    assert regular_file_content_type(file) == RUNTIME_RESOURCE_CONTENT_TYPE


def test_regular_file_content_type_preserves_real_resource_mime() -> None:
    file = IndexInputFile(
        path="assets/report.pdf",
        content_type="application/pdf",
        content=b"report",
        size=6,
    )

    assert regular_file_content_type(file) == "application/pdf"


async def test_poison_markdown_reclassification_clears_note_only_state(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
) -> None:
    project_id = relation_repository.project_id
    assert project_id is not None
    now = datetime.now(tz=UTC)
    poison_path = "_phase7_import/.md"

    poison = Entity(
        project_id=project_id,
        title="Poison note",
        note_type="note",
        entity_metadata={"status": "draft"},
        content_type="text/markdown",
        permalink="poison-note",
        file_path=poison_path,
        checksum="old-checksum",
        created_at=now,
        updated_at=now,
    )
    source = Entity(
        project_id=project_id,
        title="Source note",
        note_type="note",
        content_type="text/markdown",
        permalink="source-note",
        file_path="notes/source.md",
        checksum="source-checksum",
        created_at=now,
        updated_at=now,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        poison = await entity_repository.add(session, poison)
        source = await entity_repository.add(session, source)
        await entity_service.observation_repository.add(
            session,
            Observation(
                project_id=project_id,
                entity_id=poison.id,
                content="stale observation",
                category="note",
            ),
        )
        await relation_repository.add(
            session,
            Relation(
                project_id=project_id,
                from_id=poison.id,
                to_name="old-target",
                relation_type="links-to",
                generation=1,
            ),
        )
        inbound = await relation_repository.add(
            session,
            Relation(
                project_id=project_id,
                from_id=source.id,
                to_id=poison.id,
                to_name="poison-note",
                relation_type="links-to",
                generation=1,
            ),
        )
        await NoteContentRepository(project_id=project_id).create(
            session,
            {
                "entity_id": poison.id,
                "markdown_content": "# Poison note",
                "db_version": 1,
                "db_checksum": "old-checksum",
                "file_write_status": "synced",
            },
        )
        session.add(
            RelationSearchRefresh(
                project_id=project_id,
                entity_id=poison.id,
            )
        )

    batch_indexer = BatchIndexer(
        project_id=project_id,
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        observation_repository=entity_service.observation_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_writer=StorageIndexFileWriter(storage=file_service),
        session_maker=search_service.session_maker,
    )
    result = await batch_indexer.index_files(
        {
            poison_path: IndexInputFile(
                path=poison_path,
                content_type="text/markdown",
                content=b"poison object",
                size=13,
            )
        },
        max_concurrent=1,
    )

    assert result.errors == []
    async with db.scoped_session(search_service.session_maker) as session:
        repaired = await entity_repository.get_by_id(session, poison.id)
        note_content = await NoteContentRepository(project_id=project_id).get_by_entity_id(
            session,
            poison.id,
        )
        observations = await entity_service.observation_repository.find_by_entity(
            session,
            poison.id,
        )
        refreshed_inbound = await relation_repository.select_by_id(session, inbound.id)
        poison_refreshes = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=poison.id,
        )
        source_refreshes = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=source.id,
        )

    assert repaired is not None
    assert repaired.id == poison.id
    assert repaired.content_type == RUNTIME_RESOURCE_CONTENT_TYPE
    assert repaired.permalink is None
    assert repaired.note_type == "file"
    assert repaired.title == ".md"
    assert repaired.entity_metadata == {}
    assert note_content is None
    assert observations == []
    assert repaired.outgoing_relations == []
    assert refreshed_inbound is not None
    assert refreshed_inbound.to_id is None
    assert poison_refreshes == []
    assert [refresh.entity_id for refresh in source_refreshes] == [source.id]
