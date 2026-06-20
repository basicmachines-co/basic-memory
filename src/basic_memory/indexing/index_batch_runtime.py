"""Portable runtime for indexing loaded file batches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.input_file_adaptation import (
    IndexContentTypeProvider,
    LoadedIndexFile,
    build_index_input_files,
)
from basic_memory.indexing.models import IndexedEntity, IndexingBatchResult, IndexInputFile
from basic_memory.indexing.note_content_batch_reconciliation import (
    IndexedNoteContentEntity,
    IndexedNoteContentEntityRepository,
    IndexedNoteContentObservedAt,
    IndexedNoteContentReconciler,
    reconcile_indexed_note_content_batch,
)


class IndexInputBatchExecutor(Protocol):
    """Capability that can index one loaded input-file batch."""

    async def index_files(
        self,
        files: Mapping[str, IndexInputFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
    ) -> IndexingBatchResult:
        """Index one batch of storage-neutral input files."""


@dataclass(frozen=True, slots=True)
class IndexBatchRuntime[EntityT: IndexedNoteContentEntity, FileInfoT: LoadedIndexFile]:
    """Run the shared index-plus-note-content sequence over injected capabilities."""

    batch_indexer: IndexInputBatchExecutor
    content_type_provider: IndexContentTypeProvider
    entity_repository: IndexedNoteContentEntityRepository[EntityT]
    session_maker: async_sessionmaker[AsyncSession]
    note_content_reconciler: IndexedNoteContentReconciler[EntityT]
    observed_at_for_indexed: IndexedNoteContentObservedAt[FileInfoT]
    note_content_source: str = "index"

    async def index_loaded_files(
        self,
        files: Mapping[str, FileInfoT],
        *,
        max_concurrent: int = 8,
        parse_max_concurrent: int | None = None,
        metadata_update_max_concurrent: int | None = None,
    ) -> IndexingBatchResult:
        """Index loaded files and reconcile note_content rows from final markdown."""
        input_files = build_index_input_files(
            files,
            content_type_provider=self.content_type_provider,
        )
        result = await self.batch_indexer.index_files(
            input_files,
            max_concurrent=max_concurrent,
            parse_max_concurrent=parse_max_concurrent,
        )
        note_content_errors = await reconcile_indexed_note_content_batch(
            result.indexed,
            file_infos=files,
            entity_repository=self.entity_repository,
            session_maker=self.session_maker,
            note_content_reconciler=self.note_content_reconciler,
            observed_at_for_indexed=self.observed_at_for_indexed,
            max_concurrent=metadata_update_max_concurrent or max_concurrent,
            source=self.note_content_source,
        )
        result.errors.extend(error.as_tuple() for error in note_content_errors)
        result.search_indexed = count_search_indexed_entities(result.indexed)
        return result


def count_search_indexed_entities(indexed_entities: list[IndexedEntity]) -> int:
    """Count entities that entered the markdown search path."""
    return sum(1 for indexed in indexed_entities if indexed.markdown_content is not None)
