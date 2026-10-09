"""Wiki projector pages keep their search rows and relations but get no vectors.

The projector rewrites a folder's generated index.md and log.md after every
accepted note change. Those pages are link lists and change logs, so embedding
them costs worker time and adds noise to semantic results. A page counts as
projector-owned only while its current accepted revision came from the
projector (note_content.last_source), never by file name: a hand-written
index.md is embedded, and so is a generated page once someone edits it.

These tests run the real SearchService and search repository against the real
database backend (SQLite, or Postgres with BASIC_MEMORY_TEST_POSTGRES=1). Only
the embedding model is a deterministic stub, so no model is loaded.
"""

from datetime import datetime, timezone
from hashlib import sha256

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.indexing.wiki_projector import WIKI_PROJECTOR_SOURCE
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType
from basic_memory.services import FileService
from basic_memory.services.search_service import SearchService

SessionMaker = async_sessionmaker[AsyncSession]

INDEX_MARKDOWN = "# Notes\n\n## Notes\n\n- [[notes/coffee|Coffee]]\n- [[notes/tea|Tea]]\n"
EDITED_INDEX_MARKDOWN = INDEX_MARKDOWN + "\nMy own summary of these brewing notes.\n"
NOTE_MARKDOWN = "# Coffee\n\nPour over brings out the bright notes of a light roast.\n"


class StubEmbeddingProvider:
    """Deterministic four-dimension provider so no model is loaded."""

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0, 1.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}


@pytest.fixture
async def semantic_search_service(
    session_maker: SessionMaker,
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
) -> SearchService:
    """SearchService over the backend's semantic search repository."""
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        async with db.scoped_session(session_maker) as session:
            try:
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await session.commit()
            except DBAPIError:
                pytest.skip("pgvector extension is unavailable in this Postgres environment.")
        repository: SearchRepositoryBase = PostgresSearchRepository(
            session_maker,
            project_id=test_project.id,
            app_config=app_config,
            embedding_provider=StubEmbeddingProvider(),
        )
    else:
        pytest.importorskip("sqlite_vec")
        repository = SQLiteSearchRepository(
            session_maker,
            project_id=test_project.id,
            app_config=app_config,
            embedding_provider=StubEmbeddingProvider(),
        )
    await repository.init_search_index()
    # Some Python builds import sqlite_vec but cannot load SQLite extensions;
    # init then falls back to keyword-only search instead of raising (#711).
    if not repository._semantic_enabled:
        pytest.skip("SQLite extension loading is unavailable; semantic search fell back.")
    return SearchService(repository, entity_repository, file_service, session_maker)


async def _create_entity(
    session_maker: SessionMaker,
    entity_repository: EntityRepository,
    *,
    title: str,
    file_path: str,
) -> Entity:
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": title,
                "note_type": "note",
                "permalink": file_path.removesuffix(".md"),
                "file_path": file_path,
                "content_type": "text/markdown",
                "checksum": sha256(file_path.encode()).hexdigest(),
                "created_at": now,
                "updated_at": now,
            },
        )


async def _accept(
    session_maker: SessionMaker,
    project: Project,
    entity: Entity,
    *,
    markdown: str,
    db_version: int,
    source: str,
) -> None:
    """Record an accepted note revision the way the accepted-note write path does."""
    async with db.scoped_session(session_maker) as session:
        await NoteContentRepository(project_id=project.id).accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=entity.id,
                markdown_content=markdown,
                db_version=db_version,
                db_checksum=sha256(markdown.encode()).hexdigest(),
                last_source=source,
                updated_at=datetime.now(timezone.utc),
            ),
        )
        await session.commit()


async def _index_search_rows(
    search_service: SearchService,
    session_maker: SessionMaker,
    entity_repository: EntityRepository,
    entity: Entity,
    markdown: str,
) -> None:
    """Rebuild the entity's full-text rows, including its outgoing relations."""
    async with db.scoped_session(session_maker) as session:
        loaded = await entity_repository.find_by_id(session, entity.id)
    assert loaded is not None
    await search_service.index_entity_data(loaded, content=markdown)


async def _vector_keys(search_service: SearchService, entity_id: int) -> tuple[int, set[str]]:
    """Return manifest row count and live physical vector keys for one entity."""
    repository = search_service.repository
    assert isinstance(repository, SearchRepositoryBase)
    manifest = await repository.get_entity_chunk_manifest(entity_id)
    physical = await repository.get_entity_physical_chunk_keys(entity_id)
    return len(manifest), physical or set()


async def _search_row_keys(search_service: SearchService, entity_id: int) -> list[tuple[str, int]]:
    rows = await search_service.repository.get_entity_search_rows(entity_id)
    return sorted((row.type, row.id) for row in rows)


@pytest.mark.asyncio
async def test_projector_write_leaves_no_vectors_and_keeps_search_rows(
    semantic_search_service: SearchService,
    session_maker: SessionMaker,
    test_project: Project,
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
) -> None:
    """A generated index page is searchable and linked, but never embedded."""
    target = await _create_entity(
        session_maker, entity_repository, title="Coffee", file_path="notes/coffee.md"
    )
    index_page = await _create_entity(
        session_maker, entity_repository, title="Notes", file_path="notes/index.md"
    )
    async with db.scoped_session(session_maker) as session:
        await relation_repository.create(
            session,
            {
                "from_id": index_page.id,
                "to_id": target.id,
                "to_name": target.title,
                "relation_type": "links_to",
            },
        )
    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=1,
        source=WIKI_PROJECTOR_SOURCE,
    )
    await _index_search_rows(
        semantic_search_service, session_maker, entity_repository, index_page, INDEX_MARKDOWN
    )
    search_rows_before = await _search_row_keys(semantic_search_service, index_page.id)
    assert {row_type for row_type, _ in search_rows_before} == {
        SearchItemType.ENTITY.value,
        SearchItemType.RELATION.value,
    }

    result = await semantic_search_service.sync_entity_vectors(index_page.id)

    assert result.entities_failed == 0
    assert result.entities_skipped == 1
    assert result.embedding_jobs_total == 0
    assert await _vector_keys(semantic_search_service, index_page.id) == (0, set())
    # Full-text rows and the relation the graph uses are untouched.
    assert await _search_row_keys(semantic_search_service, index_page.id) == search_rows_before
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_entities(session, index_page.id, target.id)
    assert [relation.relation_type for relation in relations] == ["links_to"]


@pytest.mark.asyncio
async def test_existing_vectors_are_removed_once_the_projector_owns_the_page(
    semantic_search_service: SearchService,
    session_maker: SessionMaker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """Vectors embedded before the projector took the page over go on its next sync."""
    index_page = await _create_entity(
        session_maker, entity_repository, title="Notes", file_path="notes/index.md"
    )
    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=1,
        source="api",
    )
    await _index_search_rows(
        semantic_search_service, session_maker, entity_repository, index_page, INDEX_MARKDOWN
    )
    await semantic_search_service.sync_entity_vectors(index_page.id)
    manifest_rows, physical_keys = await _vector_keys(semantic_search_service, index_page.id)
    assert manifest_rows > 0
    assert physical_keys

    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=2,
        source=WIKI_PROJECTOR_SOURCE,
    )
    result = await semantic_search_service.sync_entity_vectors(index_page.id)

    assert result.entities_failed == 0
    assert result.entities_skipped == 1
    assert result.embedding_jobs_total == 0
    assert await _vector_keys(semantic_search_service, index_page.id) == (0, set())


@pytest.mark.asyncio
async def test_batch_sync_skips_and_clears_projector_pages(
    semantic_search_service: SearchService,
    session_maker: SessionMaker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """Full reindexes run the batch path, so it must skip and clean up the same way."""
    note = await _create_entity(
        session_maker, entity_repository, title="Coffee", file_path="notes/coffee.md"
    )
    index_page = await _create_entity(
        session_maker, entity_repository, title="Notes", file_path="notes/index.md"
    )
    await _accept(
        session_maker, test_project, note, markdown=NOTE_MARKDOWN, db_version=1, source="api"
    )
    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=1,
        source="api",
    )
    for entity, markdown in ((note, NOTE_MARKDOWN), (index_page, INDEX_MARKDOWN)):
        await _index_search_rows(
            semantic_search_service, session_maker, entity_repository, entity, markdown
        )
    # Both start embedded, as a generated page would be before this change shipped.
    await semantic_search_service.sync_entity_vectors_batch([note.id, index_page.id])
    note_vectors = await _vector_keys(semantic_search_service, note.id)
    assert note_vectors[0] > 0
    assert (await _vector_keys(semantic_search_service, index_page.id))[0] > 0

    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=2,
        source=WIKI_PROJECTOR_SOURCE,
    )
    result = await semantic_search_service.sync_entity_vectors_batch([note.id, index_page.id])

    assert result.entities_total == 2
    assert result.entities_failed == 0
    # The unchanged note is skipped too: its vectors are already current.
    assert result.entities_skipped == 2
    assert result.embedding_jobs_total == 0
    assert await _vector_keys(semantic_search_service, index_page.id) == (0, set())
    assert await _vector_keys(semantic_search_service, note.id) == note_vectors


@pytest.mark.asyncio
async def test_hand_written_index_page_is_embedded(
    semantic_search_service: SearchService,
    session_maker: SessionMaker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """An Obsidian-style index.md the user wrote is ordinary content, not a projection."""
    index_page = await _create_entity(
        session_maker, entity_repository, title="Index", file_path="index.md"
    )
    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=1,
        source="api",
    )
    await _index_search_rows(
        semantic_search_service, session_maker, entity_repository, index_page, INDEX_MARKDOWN
    )

    result = await semantic_search_service.sync_entity_vectors(index_page.id)

    assert result.entities_synced == 1
    assert result.entities_skipped == 0
    assert result.embedding_jobs_total > 0
    manifest_rows, physical_keys = await _vector_keys(semantic_search_service, index_page.id)
    assert manifest_rows > 0
    assert physical_keys


@pytest.mark.asyncio
async def test_projector_page_edited_by_a_user_is_embedded(
    semantic_search_service: SearchService,
    session_maker: SessionMaker,
    test_project: Project,
    entity_repository: EntityRepository,
) -> None:
    """Once someone else writes the page, it stops being projector-owned."""
    index_page = await _create_entity(
        session_maker, entity_repository, title="Notes", file_path="notes/index.md"
    )
    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=INDEX_MARKDOWN,
        db_version=1,
        source=WIKI_PROJECTOR_SOURCE,
    )
    await _index_search_rows(
        semantic_search_service, session_maker, entity_repository, index_page, INDEX_MARKDOWN
    )
    await semantic_search_service.sync_entity_vectors(index_page.id)
    assert await _vector_keys(semantic_search_service, index_page.id) == (0, set())

    await _accept(
        session_maker,
        test_project,
        index_page,
        markdown=EDITED_INDEX_MARKDOWN,
        db_version=2,
        source="mcp",
    )
    await _index_search_rows(
        semantic_search_service,
        session_maker,
        entity_repository,
        index_page,
        EDITED_INDEX_MARKDOWN,
    )
    result = await semantic_search_service.sync_entity_vectors(index_page.id)

    assert result.entities_synced == 1
    assert result.entities_skipped == 0
    assert result.embedding_jobs_total > 0
    manifest_rows, physical_keys = await _vector_keys(semantic_search_service, index_page.id)
    assert manifest_rows > 0
    assert physical_keys
