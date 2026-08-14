"""Repository and domain tests for note-level chunk inspection."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError

from basic_memory import db
from basic_memory.file_utils import FileError
from basic_memory.indexing.note_content_reconciliation import NoteContentState
from basic_memory.models import NoteContent, Project
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.config import DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.repository.search_repository_base import ChunkManifestRow
from basic_memory.repository.semantic_chunking import (
    build_entity_fingerprint,
    build_vector_chunk_records,
)
from basic_memory.services.retrieval_inspect import (
    ChunkFresh,
    ChunkFreshnessUnknown,
    ChunkIndexBehindRows,
    ChunkRowsBehindFile,
    ConfiguredVectorIdentity,
    CurrentSourceHashes,
    classify_chunk_status,
    inspect_entity_chunks,
    lineage_shows_rows_behind_file,
)
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


def _search_rows(project_id: int, entity_id: int) -> list[SearchIndexRow]:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    return [
        SearchIndexRow(
            project_id=project_id,
            id=entity_id,
            type="entity",
            file_path="notes/inspection.md",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
            title="Inspection Note",
            permalink="notes/inspection",
            content_snippet="Entity prose for inspection.",
        ),
        SearchIndexRow(
            project_id=project_id,
            id=entity_id,
            type="observation",
            file_path="notes/inspection.md",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
            title="Inspection Note",
            permalink=f"notes/inspection/observations/{entity_id}",
            entity_id=entity_id,
            category="fact",
            content_snippet="A retrieval fact.",
        ),
        SearchIndexRow(
            project_id=project_id,
            id=entity_id,
            type="relation",
            file_path="notes/inspection.md",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
            title="Inspection Note",
            permalink=f"notes/inspection/supports/{entity_id}",
            from_id=entity_id,
            entity_id=entity_id,
            relation_type="supports",
            content_snippet="Inspection Note supports Target Note",
        ),
    ]


async def _insert_manifest(
    session_maker,
    *,
    project_id: int,
    entity_id: int,
    rows: list[SearchIndexRow],
    embedding_model: str,
    vector_index: str,
    pending_key: str | None = None,
    omit_chunk_key: str | None = None,
) -> None:
    records = build_vector_chunk_records(rows).records
    # Fingerprint always covers the full current record set — omitting a record after
    # this point reproduces a scheduling pass that stored only part of the chunks.
    entity_fingerprint = build_entity_fingerprint(records)
    if omit_chunk_key is not None:
        records = [record for record in records if record["chunk_key"] != omit_chunk_key]
    updated_at = datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)
    values = [
        {
            "project_id": project_id,
            "entity_id": entity_id,
            "chunk_key": record["chunk_key"],
            "chunk_text": record["chunk_text"],
            "source_hash": record["source_hash"],
            "entity_fingerprint": entity_fingerprint,
            "embedding_model": embedding_model,
            "vector_index": vector_index,
            "embedding_status": "pending" if record["chunk_key"] == pending_key else "ready",
            "updated_at": updated_at,
        }
        for record in records
    ]
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status, updated_at"
                ") VALUES ("
                ":project_id, :entity_id, :chunk_key, :chunk_text, :source_hash, "
                ":entity_fingerprint, :embedding_model, :vector_index, "
                ":embedding_status, :updated_at)"
            ),
            values,
        )
        await session.commit()


async def _write_current_entity_file(file_service, entity, content: str = "# Inspection\n") -> str:
    """Write the entity file and align its in-memory checksum with those bytes."""
    checksum = await file_service.write_file(entity.file_path, content)
    entity.checksum = checksum
    return checksum


async def _insert_note_content(
    session_maker,
    entity,
    *,
    db_checksum: str,
    file_checksum: str,
    file_write_status: str,
) -> None:
    """Persist one lineage state through the project-scoped repository."""
    repository = NoteContentRepository(project_id=entity.project_id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(
            session,
            NoteContent(
                entity_id=entity.id,
                markdown_content="# Accepted\n",
                db_version=2,
                db_checksum=db_checksum,
                file_version=1,
                file_checksum=file_checksum,
                file_write_status=file_write_status,
            ),
        )


@pytest.mark.asyncio
async def test_inspection_groups_rows_and_normalizes_manifest_timestamps(
    search_repository,
    session_maker,
    sample_entity,
    app_config,
    tmp_path,
    file_service,
):
    """Entity, observation, and relation rows retain distinct chunk ownership."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
        pending_key=f"observation:{sample_entity.id}:0",
    )
    await _write_current_entity_file(file_service, sample_entity)

    # A second project uses the same entity/search ids and chunk key. Project-scoped
    # repository reads must not admit any of its rows into the inspection.
    async with db.scoped_session(session_maker) as session:
        second_project = await ProjectRepository().create(
            session,
            {
                "name": "inspection-other-project",
                "path": str(tmp_path / "other"),
                "is_active": True,
                "is_default": False,
            },
        )
    assert isinstance(second_project, Project)
    second_repository = create_search_repository(
        session_maker,
        project_id=second_project.id,
        app_config=app_config,
    )
    foreign_rows = _search_rows(second_project.id, sample_entity.id)
    foreign_rows[0].title = "Foreign inspection row"
    await second_repository.bulk_index_items(foreign_rows)
    await _insert_manifest(
        session_maker,
        project_id=second_project.id,
        entity_id=sample_entity.id,
        rows=foreign_rows,
        embedding_model=second_repository.configured_embedding_model,
        vector_index=second_repository.configured_vector_index,
    )

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert [row.search_row.type for row in inspection.rows] == [
        "entity",
        "observation",
        "relation",
    ]
    assert [row.chunks[0].stored_row.chunk_key for row in inspection.rows] == [
        f"entity:{sample_entity.id}:0",
        f"observation:{sample_entity.id}:0",
        f"relation:{sample_entity.id}:0",
    ]
    assert inspection.readiness.total == 3
    assert inspection.readiness.ready == 2
    assert inspection.readiness.pending == 1
    assert inspection.readiness.stale == 0
    assert inspection.readiness.orphaned == 0
    assert inspection.stale is True
    assert isinstance(inspection.freshness, ChunkIndexBehindRows)
    assert all(
        chunk.stored_row.updated_at.tzinfo is not None
        for row in inspection.rows
        for chunk in row.chunks
    )
    assert all(row.search_row.title != "Foreign inspection row" for row in inspection.rows)


class _UnusedEmbeddingProvider:
    """Satisfies repository construction; inspection never embeds."""

    model_name = "inspection-embedding"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:  # pragma: no cover - unused
        raise NotImplementedError

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError

    def runtime_log_attrs(self) -> dict[str, object]:  # pragma: no cover - unused
        return {}


class _ExtensionLoadingUnavailableDriver:
    """Model aiosqlite exposing a method absent from its wrapped sqlite3 connection."""

    async def enable_load_extension(self, enabled: bool) -> None:
        raise AttributeError("sqlite3.Connection has no attribute 'enable_load_extension'")


class _RawConnectionWithUnavailableDriver:
    driver_connection = _ExtensionLoadingUnavailableDriver()


class _AsyncConnectionWithUnavailableDriver:
    async def get_raw_connection(self) -> _RawConnectionWithUnavailableDriver:
        return _RawConnectionWithUnavailableDriver()


class _VecProbeSessionWithUnavailableDriver:
    async def execute(self, _statement) -> None:
        raise SAOperationalError(
            "SELECT vec_version()",
            {},
            RuntimeError("no such function: vec_version"),
        )

    async def connection(self) -> _AsyncConnectionWithUnavailableDriver:
        return _AsyncConnectionWithUnavailableDriver()


@pytest.mark.asyncio
async def test_sqlite_vec_probe_normalizes_wrapped_extension_loading_failure(
    session_maker,
    sample_entity,
    app_config,
):
    """An aiosqlite wrapper must not leak its inner connection's AttributeError."""
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        pytest.skip("SQLite extension loading is SQLite-specific")
    search_repository = SQLiteSearchRepository(
        session_maker,
        project_id=sample_entity.project_id,
        app_config=app_config,
    )

    with pytest.raises(
        SemanticDependenciesMissingError,
        match="does not support SQLite extension loading",
    ) as exc_info:
        await search_repository._ensure_sqlite_vec_loaded(_VecProbeSessionWithUnavailableDriver())

    assert isinstance(exc_info.value.__cause__, AttributeError)


def _sqlite_vec_loadable() -> bool:
    """The keyword-only runtime fallback exists precisely because this can be False."""
    import sqlite3

    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        return False
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    return True


def _skip_unless_healthy_semantic_runtime(app_config) -> None:
    """Healthy-runtime semantic tests need real sqlite-vec on the SQLite backend."""
    if app_config.database_backend != DatabaseBackend.POSTGRES and not _sqlite_vec_loadable():
        pytest.skip("healthy-runtime semantic inspection needs loadable sqlite-vec")


def _semantic_repository(session_maker, project_id: int, app_config):
    """Build a semantic-enabled repository; the shared fixture disables semantic search."""
    config = app_config.model_copy(update={"semantic_search_enabled": True})
    repository_type = (
        PostgresSearchRepository
        if config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    return repository_type(
        session_maker,
        project_id=project_id,
        app_config=config,
        embedding_provider=_UnusedEmbeddingProvider(),
    )


async def _insert_physical_vectors(
    session_maker,
    repository,
    *,
    omit_chunk_key: str | None = None,
) -> None:
    """Materialize a physical vector row for every stored manifest chunk.

    Callers must run ``repository._ensure_vector_tables()`` before inserting their
    manifest: storage creation resets ready manifest rows to pending by design.
    """
    async with db.scoped_session(session_maker) as session:
        manifest_result = await session.execute(
            text(
                "SELECT id, chunk_key, source_hash FROM search_vector_chunks "
                "WHERE project_id = :project_id"
            ),
            {"project_id": repository.project_id},
        )
        manifest_rows = [
            row for row in manifest_result.mappings().all() if row["chunk_key"] != omit_chunk_key
        ]
        if isinstance(repository, PostgresSearchRepository):
            for row in manifest_rows:
                await session.execute(
                    text(
                        "INSERT INTO search_vector_embeddings ("
                        "chunk_id, project_id, embedding, embedding_dims, source_hash"
                        ") VALUES ("
                        ":chunk_id, :project_id, CAST(:embedding AS vector), 4, :source_hash)"
                    ),
                    {
                        "chunk_id": row["id"],
                        "project_id": repository.project_id,
                        "embedding": "[0.1, 0.1, 0.1, 0.1]",
                        "source_hash": row["source_hash"],
                    },
                )
        else:
            import sqlite_vec

            await repository._ensure_sqlite_vec_loaded(session)
            embedding = sqlite_vec.serialize_float32([0.1, 0.1, 0.1, 0.1])
            for row in manifest_rows:
                await session.execute(
                    text(
                        "INSERT INTO search_vector_embeddings (rowid, embedding, source_hash) "
                        "VALUES (:rowid, :embedding, :source_hash)"
                    ),
                    {
                        "rowid": row["id"],
                        "embedding": embedding,
                        "source_hash": row["source_hash"],
                    },
                )
        await session.commit()


@pytest.mark.asyncio
async def test_current_chunks_missing_from_manifest_mark_index_behind(
    session_maker,
    sample_entity,
    app_config,
    file_service,
):
    """A manifest covering only part of the current chunks must not report fresh."""
    _skip_unless_healthy_semantic_runtime(app_config)
    search_repository = _semantic_repository(session_maker, sample_entity.project_id, app_config)
    await search_repository._ensure_vector_tables()
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    # The stored rows carry the full current fingerprint, so per-chunk and fingerprint
    # comparisons alone would call this entity fresh while a chunk is unavailable.
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
        omit_chunk_key=f"relation:{sample_entity.id}:0",
    )
    await _insert_physical_vectors(session_maker, search_repository)
    await _write_current_entity_file(file_service, sample_entity)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.readiness.total == 2
    assert inspection.readiness.ready == 2
    assert inspection.readiness.missing == 1
    assert inspection.stale is True
    assert isinstance(inspection.freshness, ChunkIndexBehindRows)
    assert inspection.freshness.missing_chunk_count == 1
    assert inspection.freshness.entity_fingerprint_indexed == (
        inspection.freshness.entity_fingerprint_current
    )


@pytest.mark.asyncio
async def test_embed_opt_out_note_is_not_missing_coverage(
    session_maker,
    sample_entity,
    app_config,
    file_service,
):
    """A note that opts out of embeddings has no expected chunks to miss."""
    search_repository = _semantic_repository(session_maker, sample_entity.project_id, app_config)
    sample_entity.entity_metadata = {"embed": False}
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _write_current_entity_file(file_service, sample_entity)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.readiness.missing == 0
    assert inspection.readiness.total == 0
    assert inspection.stale is False
    assert isinstance(inspection.freshness, ChunkFresh)


@pytest.mark.asyncio
async def test_ready_chunk_without_physical_vector_reports_orphaned(
    session_maker,
    sample_entity,
    app_config,
    file_service,
):
    """A manifest-ready chunk whose physical vector row is gone cannot be served."""
    _skip_unless_healthy_semantic_runtime(app_config)
    search_repository = _semantic_repository(session_maker, sample_entity.project_id, app_config)
    await search_repository._ensure_vector_tables()
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    await _insert_physical_vectors(
        session_maker,
        search_repository,
        omit_chunk_key=f"entity:{sample_entity.id}:0",
    )
    await _write_current_entity_file(file_service, sample_entity)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    statuses = {
        chunk.stored_row.chunk_key: chunk.status for row in inspection.rows for chunk in row.chunks
    }
    assert statuses[f"entity:{sample_entity.id}:0"] == "orphaned"
    assert statuses[f"observation:{sample_entity.id}:0"] == "ready"
    assert statuses[f"relation:{sample_entity.id}:0"] == "ready"
    assert inspection.readiness.orphaned == 1
    assert inspection.readiness.ready == 2
    assert inspection.readiness.missing == 0
    assert inspection.stale is True
    assert isinstance(inspection.freshness, ChunkIndexBehindRows)


@pytest.mark.asyncio
async def test_postgres_physical_probe_uses_active_search_path(
    session_maker,
    sample_entity,
    app_config,
    monkeypatch,
):
    """Tables outside the active search path must not trigger an unqualified join."""
    if app_config.database_backend != DatabaseBackend.POSTGRES:
        pytest.skip("search_path is PostgreSQL-specific")

    search_repository = _semantic_repository(
        session_maker,
        sample_entity.project_id,
        app_config,
    )
    await search_repository._ensure_vector_tables()

    async with db.scoped_session(session_maker) as session:
        await session.execute(text("CREATE SCHEMA inspect_chunks_empty"))
        await session.execute(text("SET LOCAL search_path TO inspect_chunks_empty"))

        @asynccontextmanager
        async def use_active_session(_session_maker):
            yield session

        monkeypatch.setattr(
            "basic_memory.repository.postgres_search_repository.db.scoped_session",
            use_active_session,
        )

        assert await search_repository.get_entity_physical_chunk_keys(sample_entity.id) == set()


@pytest.mark.asyncio
async def test_runtime_semantic_fallback_keeps_inspection_manifest_only(
    session_maker,
    sample_entity,
    app_config,
    file_service,
    monkeypatch,
):
    """A vec-less host with enabled config must not invent missing chunks or crash."""
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        pytest.skip("the keyword-only runtime fallback is SQLite-specific")
    search_repository = _semantic_repository(session_maker, sample_entity.project_id, app_config)
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    # A vec-less host keeps the embeddings table from an earlier working install; a
    # plain stand-in reproduces that on-disk shape without loading sqlite-vec, so
    # this test runs on the exact keyword-only environment it models.
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS search_vector_embeddings "
                "(embedding BLOB, source_hash TEXT)"
            )
        )
        await session.commit()
    await _write_current_entity_file(file_service, sample_entity)

    async def vec_unavailable(session):
        raise SemanticDependenciesMissingError("sqlite-vec unavailable on this host")

    monkeypatch.setattr(search_repository, "_ensure_sqlite_vec_loaded", vec_unavailable)

    assert await search_repository.semantic_effectively_enabled() is False
    assert await search_repository.get_entity_physical_chunk_keys(sample_entity.id) is None

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.readiness.missing == 0
    assert inspection.readiness.ready == 3
    assert isinstance(inspection.freshness, ChunkFresh)


@pytest.mark.asyncio
async def test_effective_semantic_signal_defaults_to_config_without_runtime_probe(
    session_maker,
    sample_entity,
    app_config,
):
    """Backends without a runtime fallback answer from configuration alone."""
    enabled_config = app_config.model_copy(update={"semantic_search_enabled": True})
    enabled = PostgresSearchRepository(
        session_maker,
        project_id=sample_entity.project_id,
        app_config=enabled_config,
        embedding_provider=_UnusedEmbeddingProvider(),
    )
    assert await enabled.semantic_effectively_enabled() is True

    disabled = PostgresSearchRepository(
        session_maker,
        project_id=sample_entity.project_id,
        app_config=app_config.model_copy(update={"semantic_search_enabled": False}),
    )
    assert await disabled.semantic_effectively_enabled() is False


@pytest.mark.asyncio
async def test_semantic_disabled_inspection_does_not_validate_dormant_embedding_config(
    session_maker,
    sample_entity,
    app_config,
    file_service,
):
    """Rows-only inspection must not require a valid dormant embedding identity."""
    config = app_config.model_copy(
        update={
            "semantic_search_enabled": False,
            "semantic_embedding_provider": "litellm",
            "semantic_embedding_model": "cohere/embed-english-v3.0",
            "semantic_embedding_dimensions": None,
        }
    )
    search_repository = create_search_repository(
        session_maker,
        project_id=sample_entity.project_id,
        app_config=config,
    )
    await search_repository.bulk_index_items(
        _search_rows(sample_entity.project_id, sample_entity.id)[:1]
    )
    await _write_current_entity_file(file_service, sample_entity)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.configured_identity.embedding_model == "disabled"
    assert inspection.configured_identity.semantic_enabled is False
    assert inspection.rows
    assert inspection.readiness.missing == 0


@pytest.mark.asyncio
async def test_inspection_marks_manifest_stale_after_search_row_changes(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
):
    """Changing source search text without re-chunking exposes stale stored chunks."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    await _write_current_entity_file(file_service, sample_entity)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_index SET content_snippet = :content "
                "WHERE project_id = :project_id AND type = 'observation' "
                "AND id = :row_id"
            ),
            {
                "content": "The search projection changed after chunking.",
                "project_id": sample_entity.project_id,
                "row_id": sample_entity.id,
            },
        )
        await session.commit()

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.stale is True
    assert inspection.readiness.stale == 3
    assert inspection.readiness.ready == 0
    assert isinstance(inspection.freshness, ChunkIndexBehindRows)


@pytest.mark.asyncio
async def test_source_hash_mismatch_marks_note_index_behind(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
):
    """Per-chunk stale evidence must agree with note-level freshness."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    await _write_current_entity_file(file_service, sample_entity)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET source_hash = :source_hash "
                "WHERE project_id = :project_id AND chunk_key = :chunk_key"
            ),
            {
                "source_hash": "stale-source-hash",
                "project_id": sample_entity.project_id,
                "chunk_key": f"observation:{sample_entity.id}:0",
            },
        )
        await session.commit()

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.entity_fingerprint_indexed == inspection.entity_fingerprint_current
    assert inspection.readiness.stale == 1
    assert inspection.stale is True
    assert isinstance(inspection.freshness, ChunkIndexBehindRows)


@pytest.mark.asyncio
async def test_inspection_uses_all_stored_fingerprints_for_note_staleness(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
):
    """A mixed shard manifest is stale even when its first row has the current fingerprint."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET entity_fingerprint = :fingerprint "
                "WHERE project_id = :project_id AND chunk_key = :chunk_key"
            ),
            {
                "fingerprint": "older-shard-fingerprint",
                "project_id": sample_entity.project_id,
                "chunk_key": f"relation:{sample_entity.id}:0",
            },
        )
        await session.commit()

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.entity_fingerprint_indexed == tuple(
        sorted({inspection.entity_fingerprint_current, "older-shard-fingerprint"})
    )
    assert inspection.stale is True
    assert inspection.readiness.ready == 2
    assert inspection.readiness.stale == 1


@pytest.mark.asyncio
async def test_inspection_marks_wrong_configured_identity_orphaned(
    session_maker,
    sample_entity,
    app_config,
    file_service,
):
    """A manifest row owned by another model is invisible to current retrieval."""
    _skip_unless_healthy_semantic_runtime(app_config)
    search_repository = _semantic_repository(session_maker, sample_entity.project_id, app_config)
    await search_repository._ensure_vector_tables()
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    await _insert_physical_vectors(session_maker, search_repository)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_model = 'LegacyEmbedding:model' "
                "WHERE project_id = :project_id AND chunk_key = :chunk_key"
            ),
            {
                "project_id": sample_entity.project_id,
                "chunk_key": f"entity:{sample_entity.id}:0",
            },
        )
        await session.commit()
    await _write_current_entity_file(file_service, sample_entity)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.readiness.orphaned == 1
    assert inspection.readiness.ready == 2
    assert inspection.rows[0].chunks[0].status == "orphaned"
    assert inspection.stale is True
    assert isinstance(inspection.freshness, ChunkIndexBehindRows)


@pytest.mark.asyncio
async def test_freshness_marks_missing_entity_checksum_as_rows_behind_file(
    search_repository,
    sample_entity,
    file_service,
):
    """A missing entity checksum means the indexing pass has not finalized its rows."""
    await search_repository.bulk_index_items(
        _search_rows(sample_entity.project_id, sample_entity.id)[:1]
    )
    await file_service.write_file(sample_entity.file_path, "# Unfinished sync\n")

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkRowsBehindFile)
    assert inspection.freshness.evidence.entity_checksum is None


@pytest.mark.asyncio
async def test_freshness_is_fresh_for_untouched_file_rows_and_manifest(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
):
    """Matching file, entity rows, and manifest produce the positive fresh state."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    await _write_current_entity_file(file_service, sample_entity)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkFresh)


@pytest.mark.asyncio
async def test_freshness_hashes_binary_entities_without_decoding(
    search_repository,
    sample_entity,
    file_service,
):
    """Binary resource checksums are readable even when their bytes are not UTF-8."""
    sample_entity.file_path = "assets/inspection.png"
    path = file_service.get_entity_path(sample_entity)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00binary")
    sample_entity.checksum = await file_service.compute_checksum(sample_entity.file_path)
    await search_repository.bulk_index_items(
        [
            replace(
                _search_rows(sample_entity.project_id, sample_entity.id)[0],
                file_path=sample_entity.file_path,
            )
        ]
    )

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkFresh)
    assert inspection.freshness.value == "fresh"


@pytest.mark.asyncio
async def test_freshness_marks_file_edit_after_index_as_rows_behind_file(
    search_repository,
    sample_entity,
    file_service,
):
    """An unsynchronized direct edit makes the file checksum newer than entity rows."""
    await search_repository.bulk_index_items(
        _search_rows(sample_entity.project_id, sample_entity.id)[:1]
    )
    indexed_checksum = await _write_current_entity_file(file_service, sample_entity)
    file_service.get_entity_path(sample_entity).write_bytes(b"# Edited after indexing\n")

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkRowsBehindFile)
    assert inspection.freshness.evidence.entity_checksum == indexed_checksum
    assert inspection.freshness.evidence.current_file_checksum != indexed_checksum


@pytest.mark.asyncio
async def test_freshness_uses_conclusive_lineage_when_file_cannot_be_read(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
    monkeypatch,
):
    """A recorded external-file checksum proves the rows trail inaccessible storage."""
    await search_repository.bulk_index_items(
        _search_rows(sample_entity.project_id, sample_entity.id)[:1]
    )
    sample_entity.checksum = "rows-checksum"
    await _insert_note_content(
        session_maker,
        sample_entity,
        db_checksum="accepted-db-checksum",
        file_checksum="external-file-checksum",
        file_write_status="external_change_detected",
    )

    async def fail_checksum(_path):
        raise FileError("storage unavailable")

    monkeypatch.setattr(file_service, "compute_checksum", fail_checksum)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkRowsBehindFile)
    assert inspection.freshness.evidence.current_file_checksum is None
    assert inspection.freshness.evidence.db_checksum == "accepted-db-checksum"
    assert inspection.freshness.evidence.file_checksum == "external-file-checksum"
    assert inspection.freshness.evidence.file_write_status == "external_change_detected"


@pytest.mark.asyncio
async def test_freshness_rows_behind_file_takes_precedence_over_stale_manifest(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
):
    """The upstream file divergence dominates a simultaneous manifest mismatch."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    await _write_current_entity_file(file_service, sample_entity)
    file_service.get_entity_path(sample_entity).write_bytes(b"# Newer file\n")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_index SET content_snippet = :content "
                "WHERE project_id = :project_id AND type = 'entity' AND id = :entity_id"
            ),
            {
                "content": "Newer rows than the stored chunks.",
                "project_id": sample_entity.project_id,
                "entity_id": sample_entity.id,
            },
        )
        await session.commit()

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.stale is True
    assert isinstance(inspection.freshness, ChunkRowsBehindFile)


@pytest.mark.asyncio
async def test_freshness_is_unknown_when_file_read_fails_with_clean_lineage(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
    monkeypatch,
):
    """Historical agreement cannot prove current inaccessible file bytes are unchanged."""
    await search_repository.bulk_index_items(
        _search_rows(sample_entity.project_id, sample_entity.id)[:1]
    )
    sample_entity.checksum = "synced-checksum"
    await _insert_note_content(
        session_maker,
        sample_entity,
        db_checksum="synced-checksum",
        file_checksum="synced-checksum",
        file_write_status="synced",
    )

    async def fail_checksum(_path):
        raise FileError("permission denied")

    monkeypatch.setattr(file_service, "compute_checksum", fail_checksum)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkFreshnessUnknown)


@pytest.mark.asyncio
async def test_unknown_file_freshness_preserves_stale_manifest_evidence(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
    monkeypatch,
):
    """Unreadable file bytes do not erase independently proven row-to-index lag."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )
    sample_entity.checksum = "synced-checksum"
    await _insert_note_content(
        session_maker,
        sample_entity,
        db_checksum="synced-checksum",
        file_checksum="synced-checksum",
        file_write_status="synced",
    )
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_index SET content_snippet = :content "
                "WHERE project_id = :project_id AND type = 'entity' AND id = :entity_id"
            ),
            {
                "content": "Newer rows than the stored chunks.",
                "project_id": sample_entity.project_id,
                "entity_id": sample_entity.id,
            },
        )
        await session.commit()

    async def fail_checksum(_path):
        raise FileError("storage unavailable")

    monkeypatch.setattr(file_service, "compute_checksum", fail_checksum)

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert isinstance(inspection.freshness, ChunkFreshnessUnknown)
    assert inspection.stale is True
    assert inspection.readiness.stale == 3
    assert inspection.entity_fingerprint_indexed != inspection.entity_fingerprint_current


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (NoteContentState(1, "file", 1, "file", "synced"), True),
        (
            NoteContentState(2, "accepted", 1, "external", "external_change_detected"),
            True,
        ),
        (NoteContentState(2, "accepted", 1, "old", "pending"), False),
        (NoteContentState(2, "accepted", 1, "old", "writing"), False),
        (NoteContentState(2, "accepted", 1, "old", "failed"), False),
        (NoteContentState(1, "accepted", 1, "different", "synced"), False),
        (
            NoteContentState(1, "accepted", 1, "accepted", "external_change_detected"),
            False,
        ),
    ],
)
def test_lineage_only_uses_checksums_observed_by_reconciliation(
    state: NoteContentState,
    expected: bool,
):
    """Only synced or conflict-observed file lineage can prove an upstream mismatch."""
    assert (
        lineage_shows_rows_behind_file(
            entity_checksum="rows",
            note_content=state,
        )
        is expected
    )


def test_lineage_without_note_content_is_inconclusive():
    assert not lineage_shows_rows_behind_file(
        entity_checksum="rows",
        note_content=None,
    )


def test_classify_chunk_status_covers_closed_status_space():
    """The pure classifier owns all four mutually exclusive chunk states."""
    current = CurrentSourceHashes(
        by_chunk_key={"entity:1:0": "current-source"},
        entity_fingerprint="current-entity",
    )
    identity = ConfiguredVectorIdentity(
        embedding_model="ConfiguredEmbedding:model",
        vector_index="sqlite-vec",
    )
    stored = ChunkManifestRow(
        entity_id=1,
        chunk_key="entity:1:0",
        chunk_text="content",
        source_hash="current-source",
        entity_fingerprint="current-entity",
        embedding_model=identity.embedding_model,
        vector_index=identity.vector_index,
        embedding_status="ready",
        updated_at=datetime.now(timezone.utc),
    )

    assert classify_chunk_status(stored, current, identity, {"entity:1:0"}) == "ready"
    # None = physical storage not inspectable (semantic disabled or external index):
    # status stays manifest-only.
    assert classify_chunk_status(stored, current, identity, None) == "ready"
    disabled_identity = ConfiguredVectorIdentity(
        embedding_model="disabled",
        vector_index=identity.vector_index,
        semantic_enabled=False,
    )
    assert (
        classify_chunk_status(
            replace(stored, embedding_model="dormant-model"),
            current,
            disabled_identity,
            None,
        )
        == "ready"
    )
    # A ready manifest row whose physical vector row is gone can never be served.
    assert classify_chunk_status(stored, current, identity, set()) == "orphaned"
    assert classify_chunk_status(
        replace(stored, embedding_status="pending"), current, identity, set()
    ) == ("pending")
    assert (
        classify_chunk_status(replace(stored, source_hash="old"), current, identity, None)
        == "stale"
    )
    assert (
        classify_chunk_status(replace(stored, vector_index="milvus"), current, identity, None)
        == "orphaned"
    )


def test_chunk_manifest_row_hydrates_string_timestamp_and_rejects_unknown_status():
    """Portable hydration normalizes SQLite timestamps and validates persisted status."""
    row = {
        "entity_id": 1,
        "chunk_key": "entity:1:0",
        "chunk_text": "content",
        "source_hash": "source",
        "entity_fingerprint": "entity",
        "embedding_model": "model",
        "vector_index": "sqlite-vec",
        "embedding_status": "ready",
        "updated_at": "2026-08-12T12:30:00+00:00",
    }

    hydrated = ChunkManifestRow.from_mapping(row)
    assert hydrated.updated_at.tzinfo is not None

    with pytest.raises(ValueError, match="Unknown vector chunk embedding status"):
        ChunkManifestRow.from_mapping({**row, "embedding_status": "broken"})


@pytest.mark.asyncio
async def test_duplicate_logical_search_rows_collapse_to_one_inspected_row(
    search_repository,
    session_maker,
    sample_entity,
    file_service,
):
    """SQLite FTS duplicates of one logical row must not double displayed chunks."""
    rows = _search_rows(sample_entity.project_id, sample_entity.id)
    await search_repository.bulk_index_items(rows)
    # bulk_index_items assumes prior deletion; indexing again fabricates the duplicate
    # logical (type, id) copies the finding describes.
    await search_repository.bulk_index_items(rows)
    await _insert_manifest(
        session_maker,
        project_id=sample_entity.project_id,
        entity_id=sample_entity.id,
        rows=rows,
        embedding_model=search_repository.configured_embedding_model,
        vector_index=search_repository.configured_vector_index,
    )

    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    row_keys = [(row.search_row.type, row.search_row.id) for row in inspection.rows]
    assert len(row_keys) == len(set(row_keys))
    displayed_chunks = sum(len(row.chunks) for row in inspection.rows) + sum(
        len(detached.chunks) for detached in inspection.detached
    )
    assert displayed_chunks == inspection.readiness.total


@pytest.mark.asyncio
async def test_absent_search_projection_reports_not_indexed_not_fresh(
    search_repository,
    sample_entity,
    file_service,
):
    """Two empty projections are a missing layer, never a vacuous freshness match."""
    inspection = await inspect_entity_chunks(search_repository, sample_entity, file_service)

    assert inspection.freshness.value == "not_indexed"
    assert inspection.rows == ()
    assert inspection.readiness.total == 0
