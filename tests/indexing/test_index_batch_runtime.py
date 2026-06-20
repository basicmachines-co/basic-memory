"""Tests for the portable loaded-file batch indexing runtime."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import AsyncIterator, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.note_content_batch_reconciliation as batch_reconciliation_module
from basic_memory.indexing.index_batch_runtime import (
    IndexBatchRuntime,
    count_search_indexed_entities,
)
from basic_memory.indexing.models import IndexedEntity, IndexingBatchResult, IndexInputFile


@dataclass(frozen=True, slots=True)
class FakeFileInfo:
    size: int
    checksum: str
    last_modified: datetime | None
    content: bytes | None


class PathContentTypeProvider:
    def content_type(self, path: str) -> str:
        if path.endswith(".md"):
            return "text/markdown"
        return "application/octet-stream"


@dataclass(frozen=True, slots=True)
class FakeEntity:
    id: int


@dataclass(slots=True)
class RecordingBatchIndexer:
    result: IndexingBatchResult
    calls: list[dict[str, IndexInputFile]] = field(default_factory=list)
    max_concurrent: int | None = None
    parse_max_concurrent: int | None = None

    async def index_files(
        self,
        files: Mapping[str, IndexInputFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
    ) -> IndexingBatchResult:
        self.calls.append(dict(files))
        self.max_concurrent = max_concurrent
        self.parse_max_concurrent = parse_max_concurrent
        return self.result


@dataclass(slots=True)
class FakeEntityRepository:
    entities: list[FakeEntity]
    loaded_ids: list[int] = field(default_factory=list)

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> list[FakeEntity]:
        assert session is not None
        self.loaded_ids = ids
        return self.entities


@dataclass(slots=True)
class RecordingNoteContentReconciler:
    calls: list[tuple[FakeEntity, str, datetime | None, str]] = field(default_factory=list)
    failing_entity_ids: set[int] = field(default_factory=set)

    async def reconcile(
        self,
        *,
        entity: FakeEntity,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
    ) -> None:
        self.calls.append((entity, markdown_content, observed_at, source))
        if entity.id in self.failing_entity_ids:
            raise RuntimeError(f"note_content failed for {entity.id}")


@pytest.mark.asyncio
async def test_index_batch_runtime_indexes_loaded_files_and_reconciles_note_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    session = cast(AsyncSession, object())
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    repository = FakeEntityRepository(entities=[FakeEntity(id=10), FakeEntity(id=20)])
    reconciler = RecordingNoteContentReconciler(failing_entity_ids={20})
    batch_indexer = RecordingBatchIndexer(
        result=IndexingBatchResult(
            indexed=[
                IndexedEntity(
                    path="ok.md",
                    entity_id=10,
                    permalink="ok",
                    checksum="etag-ok",
                    content_type="text/markdown",
                    markdown_content="# OK\n",
                ),
                IndexedEntity(
                    path="bad.md",
                    entity_id=20,
                    permalink="bad",
                    checksum="etag-bad",
                    content_type="text/markdown",
                    markdown_content="# Bad\n",
                ),
                IndexedEntity(
                    path="image.png",
                    entity_id=30,
                    permalink=None,
                    checksum="etag-image",
                    content_type="application/octet-stream",
                    markdown_content=None,
                ),
            ],
            errors=[("preexisting.md", "parse failed")],
            search_indexed=3,
        )
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        yield session

    def observed_at_for_indexed(
        indexed: IndexedEntity,
        file_info: FakeFileInfo | None,
    ) -> datetime | None:
        assert file_info is not None
        return file_info.last_modified

    monkeypatch.setattr(
        batch_reconciliation_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    runtime = IndexBatchRuntime(
        batch_indexer=batch_indexer,
        content_type_provider=PathContentTypeProvider(),
        entity_repository=repository,
        session_maker=session_maker,
        note_content_reconciler=reconciler,
        observed_at_for_indexed=observed_at_for_indexed,
    )
    files = {
        "ok.md": FakeFileInfo(
            size=5,
            checksum="etag-ok",
            last_modified=observed_at,
            content=b"# OK\n",
        ),
        "bad.md": FakeFileInfo(
            size=6,
            checksum="etag-bad",
            last_modified=observed_at,
            content=b"# Bad\n",
        ),
        "image.png": FakeFileInfo(
            size=3,
            checksum="etag-image",
            last_modified=None,
            content=b"png",
        ),
    }

    result = await runtime.index_loaded_files(
        files,
        max_concurrent=4,
        parse_max_concurrent=2,
        metadata_update_max_concurrent=1,
    )

    assert batch_indexer.max_concurrent == 4
    assert batch_indexer.parse_max_concurrent == 2
    assert batch_indexer.calls[0]["ok.md"] == IndexInputFile(
        path="ok.md",
        size=5,
        checksum="etag-ok",
        content_type="text/markdown",
        last_modified=observed_at,
        created_at=None,
        content=b"# OK\n",
    )
    assert batch_indexer.calls[0]["image.png"].content_type == "application/octet-stream"
    assert repository.loaded_ids == [10, 20]
    assert reconciler.calls[0] == (FakeEntity(id=10), "# OK\n", observed_at, "index")
    assert result.errors == [
        ("preexisting.md", "parse failed"),
        ("bad.md", "note_content failed for 20"),
    ]
    assert result.search_indexed == 2


def test_count_search_indexed_entities_uses_markdown_content_presence() -> None:
    assert (
        count_search_indexed_entities(
            [
                IndexedEntity(
                    path="note.md",
                    entity_id=1,
                    permalink="note",
                    checksum="etag-note",
                    markdown_content="# Note\n",
                ),
                IndexedEntity(
                    path="image.png",
                    entity_id=2,
                    permalink=None,
                    checksum="etag-image",
                    markdown_content=None,
                ),
            ]
        )
        == 1
    )
