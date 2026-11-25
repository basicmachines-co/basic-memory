"""Tests for accepted-note search repository operations."""

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.indexing.accepted_note_search import build_accepted_note_search_row
from basic_memory.repository.accepted_note_search_repository import (
    AcceptedNoteSearchRepository,
)
from basic_memory.repository.script_ngrams import build_script_ngrams
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository


class _Dialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _Bind:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = _Dialect(dialect_name)


class _RecordingSession:
    def __init__(self, *, dialect_name: str = "postgresql") -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self._bind = _Bind(dialect_name)

    def get_bind(self) -> _Bind:
        return self._bind

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.executed.append((str(statement), params))


@pytest.mark.asyncio
async def test_refresh_entity_replaces_project_scoped_hot_search_row() -> None:
    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession()
    created_at = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    updated_at = datetime(2026, 6, 18, 13, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan",
        note_type="decision",
        entity_metadata={"tags": ["strategy"]},
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body 适者生存",
        created_at=created_at,
        updated_at=updated_at,
        project_id=7,
    )

    await repository.refresh_entity(cast(AsyncSession, session), row)

    assert len(session.executed) == 4
    delete_sql, delete_params = session.executed[0]
    insert_sql, insert_params = session.executed[1]
    assert "DELETE FROM search_index" in delete_sql
    assert delete_params == {"entity_id": 42, "project_id": 7}
    assert "CAST(:metadata AS jsonb)" in insert_sql
    assert "ON CONFLICT (permalink, type, project_id)" in insert_sql
    assert "script_ngrams = EXCLUDED.script_ngrams" in insert_sql
    assert insert_params == {
        "id": 42,
        "title": "Project Plan",
        "content_stems": row.content_stems,
        "content_snippet": "Main body 适者生存",
        "script_ngrams": build_script_ngrams(row.title, row.content_stems),
        "permalink": "main/project-plan",
        "file_path": "notes/project-plan.md",
        "type": "entity",
        "metadata": '{"note_type": "decision"}',
        "entity_id": 42,
        "created_at": created_at,
        "updated_at": updated_at,
        "project_id": 7,
    }
    chunk_delete_sql, chunk_delete_params = session.executed[2]
    assert "DELETE FROM search_index_fts_chunks" in chunk_delete_sql
    assert chunk_delete_params == {
        "project_id": 7,
        "search_index_id": 42,
        "search_index_type": "entity",
    }
    chunk_sql, chunk_params = session.executed[3]
    assert "INSERT INTO search_index_fts_chunks" in chunk_sql
    assert chunk_params["project_id"] == 7
    assert json.loads(chunk_params["chunks"]) == [
        {
            "search_index_id": 42,
            "search_index_type": "entity",
            "chunk_index": 0,
            "chunk_text": "Main body 适者生存",
            "script_ngrams": build_script_ngrams("Main body 适者生存"),
        }
    ]


@pytest.mark.asyncio
async def test_refresh_entity_uses_plain_insert_for_sqlite_virtual_table() -> None:
    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession(dialect_name="sqlite")
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan",
        note_type="decision",
        entity_metadata=None,
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body",
        created_at=now,
        updated_at=now,
        project_id=7,
    )

    await repository.refresh_entity(cast(AsyncSession, session), row)

    insert_sql, _ = session.executed[1]
    assert "ON CONFLICT" not in insert_sql
    assert "CAST(:metadata AS jsonb)" not in insert_sql
    assert ":metadata" in insert_sql
    assert ":script_ngrams" in insert_sql


@pytest.mark.asyncio
async def test_refresh_entity_rejects_cross_project_rows() -> None:
    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan",
        note_type="decision",
        entity_metadata=None,
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body",
        created_at=now,
        updated_at=now,
        project_id=8,
    )

    with pytest.raises(ValueError, match="does not match repository project_id"):
        await repository.refresh_entity(cast(AsyncSession, session), row)

    assert session.executed == []


@pytest.mark.asyncio
async def test_refresh_entity_is_immediately_searchable_by_script_substring(
    search_repository,
    session_maker,
) -> None:
    repository = AcceptedNoteSearchRepository(project_id=search_repository.project_id)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Evolution",
        note_type="note",
        entity_metadata=None,
        permalink="main/evolution",
        file_path="notes/evolution.md",
        search_content="即适者生存的讨论",
        created_at=now,
        updated_at=now,
        project_id=search_repository.project_id,
    )

    async with db.scoped_session(session_maker) as session:
        await repository.refresh_entity(session, row)

    results = await search_repository.search("适者生存")

    assert [result.id for result in results] == [42]


@pytest.mark.asyncio
async def test_postgres_refresh_entity_chunks_large_script_content(
    search_repository,
    session_maker,
) -> None:
    if not isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("PostgreSQL stores full note bodies in bounded FTS chunks")

    repository = AcceptedNoteSearchRepository(project_id=search_repository.project_id)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=43,
        title="Long evolution note",
        note_type="note",
        entity_metadata=None,
        permalink="main/long-evolution",
        file_path="notes/long-evolution.md",
        search_content=f"{'進化' * 5_000}适者生存",
        created_at=now,
        updated_at=now,
        project_id=search_repository.project_id,
    )

    async with db.scoped_session(session_maker) as session:
        await repository.refresh_entity(session, row)

    results = await search_repository.search("适者生存")

    assert [result.id for result in results] == [43]


@pytest.mark.asyncio
async def test_postgres_refresh_entity_replaces_cascaded_permalink_chunks(
    search_repository,
    session_maker,
) -> None:
    if not isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("PostgreSQL cascades chunk parent keys during permalink upserts")

    repository = AcceptedNoteSearchRepository(project_id=search_repository.project_id)
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    old_row = build_accepted_note_search_row(
        entity_id=44,
        title="Old owner",
        note_type="note",
        entity_metadata=None,
        permalink="main/reassigned",
        file_path="notes/old-owner.md",
        search_content="旧所有者内容",
        created_at=now,
        updated_at=now,
        project_id=search_repository.project_id,
    )
    new_row = build_accepted_note_search_row(
        entity_id=45,
        title="New owner",
        note_type="note",
        entity_metadata=None,
        permalink="main/reassigned",
        file_path="notes/new-owner.md",
        search_content="新所有者适者生存",
        created_at=now,
        updated_at=now,
        project_id=search_repository.project_id,
    )

    async with db.scoped_session(session_maker) as session:
        await repository.refresh_entity(session, old_row)
    async with db.scoped_session(session_maker) as session:
        await repository.refresh_entity(session, new_row)

    results = await search_repository.search("适者生存")

    assert [result.id for result in results] == [45]


@pytest.mark.asyncio
async def test_refresh_entity_slices_large_fts_chunk_inserts_into_multiple_statements() -> None:
    """Large notes exceeding 200 FTS chunks or 2 MB JSON payload are written across multiple INSERT statements inside the same session."""
    from basic_memory.repository.postgres_fts_chunks import (
        POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS,
        POSTGRES_FTS_CHUNK_OVERLAP,
        POSTGRES_FTS_CHUNK_SIZE,
    )

    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    step = POSTGRES_FTS_CHUNK_SIZE - POSTGRES_FTS_CHUNK_OVERLAP
    total_chunks = POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS + 25
    large_body = "x" * ((total_chunks - 1) * step + 100)

    row = build_accepted_note_search_row(
        entity_id=99,
        title="Multi-megabyte note",
        note_type="note",
        entity_metadata=None,
        permalink="main/multi-mb-note",
        file_path="notes/multi-mb-note.md",
        search_content=large_body,
        created_at=now,
        updated_at=now,
        project_id=7,
    )

    await repository.refresh_entity(cast(AsyncSession, session), row)

    chunk_inserts = [
        (sql, params)
        for sql, params in session.executed
        if "INSERT INTO search_index_fts_chunks" in sql
    ]
    assert len(chunk_inserts) == 2
    first_batch = json.loads(chunk_inserts[0][1]["chunks"])
    second_batch = json.loads(chunk_inserts[1][1]["chunks"])
    assert len(first_batch) == POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS
    assert len(second_batch) == 25
    written_indices = [c["chunk_index"] for c in first_batch + second_batch]
    assert written_indices == list(range(total_chunks))


@pytest.mark.asyncio
async def test_postgres_replace_fts_chunks_slices_by_rows_and_byte_cap() -> None:
    """_replace_fts_chunks slices chunk batches by both row count and serialized JSON byte cap inside the caller transaction."""
    from basic_memory.repository.postgres_fts_chunks import (
        POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS,
        POSTGRES_FTS_CHUNK_OVERLAP,
        POSTGRES_FTS_CHUNK_SIZE,
        slice_postgres_fts_chunk_json_batches,
    )
    from basic_memory.repository.search_index_row import SearchIndexRow

    repo = object.__new__(PostgresSearchRepository)
    repo.project_id = 7
    session = _RecordingSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)

    step = POSTGRES_FTS_CHUNK_SIZE - POSTGRES_FTS_CHUNK_OVERLAP
    total_chunks = POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS + 15
    tail_marker = "tail_unique_token_2133"
    large_body = ("a" * ((total_chunks - 1) * step)) + tail_marker

    row = SearchIndexRow(
        id=501,
        title="Large indexed note",
        content_stems=large_body,
        content_snippet=large_body,
        permalink="main/large-indexed-note",
        file_path="notes/large-indexed-note.md",
        type="entity",
        metadata=None,
        entity_id=501,
        created_at=now,
        updated_at=now,
        project_id=7,
    )

    await repo._replace_fts_chunks(cast(AsyncSession, session), [row])

    chunk_inserts = [
        (sql, params)
        for sql, params in session.executed
        if "INSERT INTO search_index_fts_chunks" in sql
    ]
    assert len(chunk_inserts) == 2
    all_rows = [item for _, params in chunk_inserts for item in json.loads(params["chunks"])]
    assert len(all_rows) == total_chunks
    assert [r["chunk_index"] for r in all_rows] == list(range(total_chunks))
    assert tail_marker in all_rows[-1]["chunk_text"]

    heavy_chunks = [{"chunk_index": i, "chunk_text": "z" * 400_000} for i in range(7)]
    byte_sliced = slice_postgres_fts_chunk_json_batches(
        heavy_chunks, max_rows=200, max_bytes=1_000_000
    )
    assert len(byte_sliced) == 4
    assert sum(len(json.loads(b)) for b in byte_sliced) == 7


@pytest.mark.asyncio
async def test_postgres_multi_mb_note_slices_fts_chunks_and_finds_tail_text(
    search_repository,
    session_maker,
) -> None:
    if not isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("PostgreSQL stores full note bodies in bounded FTS chunks")

    from basic_memory.repository.postgres_fts_chunks import (
        POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS,
        POSTGRES_FTS_CHUNK_OVERLAP,
        POSTGRES_FTS_CHUNK_SIZE,
    )
    from basic_memory.repository.search_index_row import SearchIndexRow

    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    step = POSTGRES_FTS_CHUNK_SIZE - POSTGRES_FTS_CHUNK_OVERLAP
    # ~2.5 MB note (~420 chunks) so both >2 MB and >200-row slice boundaries are crossed.
    total_chunks = (POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS * 2) + 20
    filler = ("alpha beta gamma delta " * 260)[:step]
    accepted_tail_token = "acceptedtailneedle2133"
    indexed_tail_token = "indexedtailneedle2133"
    accepted_body = (filler * (total_chunks - 1)) + f" concluding section {accepted_tail_token}"
    indexed_body = (filler * (total_chunks - 1)) + f" concluding section {indexed_tail_token}"
    assert len(accepted_body.encode("utf-8")) > 2 * 1024 * 1024

    repository = AcceptedNoteSearchRepository(project_id=search_repository.project_id)
    accepted_row = build_accepted_note_search_row(
        entity_id=701,
        title="Multi-megabyte accepted note",
        note_type="note",
        entity_metadata=None,
        permalink="main/multi-mb-accepted-note",
        file_path="notes/multi-mb-accepted-note.md",
        search_content=accepted_body,
        created_at=now,
        updated_at=now,
        project_id=search_repository.project_id,
    )

    accepted_chunk_inserts = 0

    def _count_accepted_inserts(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        nonlocal accepted_chunk_inserts
        if "INSERT INTO search_index_fts_chunks" in statement:
            accepted_chunk_inserts += 1

    async with db.scoped_session(session_maker) as session:
        sync_engine = session.get_bind().engine
        event.listen(sync_engine, "before_cursor_execute", _count_accepted_inserts)
        try:
            await repository.refresh_entity(session, accepted_row)
        finally:
            event.remove(sync_engine, "before_cursor_execute", _count_accepted_inserts)

        accepted_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM search_index_fts_chunks "
                    "WHERE project_id = :pid AND search_index_id = 701 AND search_index_type = 'entity'"
                ),
                {"pid": search_repository.project_id},
            )
        ).scalar_one()

    assert accepted_chunk_inserts >= 3
    assert accepted_count == total_chunks
    accepted_hits = await search_repository.search(accepted_tail_token)
    assert [r.id for r in accepted_hits] == [701]

    indexed_chunk_inserts = 0

    def _count_indexed_inserts(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        nonlocal indexed_chunk_inserts
        if "INSERT INTO search_index_fts_chunks" in statement:
            indexed_chunk_inserts += 1

    sync_engine = session_maker.kw["bind"].sync_engine
    event.listen(sync_engine, "before_cursor_execute", _count_indexed_inserts)
    try:
        await search_repository.index_item(
            SearchIndexRow(
                id=702,
                title="Multi-megabyte indexed note",
                content_stems=indexed_body,
                content_snippet=indexed_body,
                permalink="main/multi-mb-indexed-note",
                file_path="notes/multi-mb-indexed-note.md",
                type="entity",
                metadata=None,
                entity_id=702,
                created_at=now,
                updated_at=now,
                project_id=search_repository.project_id,
            )
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count_indexed_inserts)

    async with db.scoped_session(session_maker) as session:
        indexed_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM search_index_fts_chunks "
                    "WHERE project_id = :pid AND search_index_id = 702 AND search_index_type = 'entity'"
                ),
                {"pid": search_repository.project_id},
            )
        ).scalar_one()

    assert indexed_chunk_inserts >= 3
    assert indexed_count == total_chunks
    indexed_hits = await search_repository.search(indexed_tail_token)
    assert [r.id for r in indexed_hits] == [702]
