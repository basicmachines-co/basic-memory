"""Portable runtime for indexing loaded file batches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.input_file_adaptation import (
    IndexContentTypeProvider,
    LoadedIndexFile,
    build_index_input_files,
)
from basic_memory.indexing.models import (
    IndexEntitySearchWriter,
    IndexedEntity,
    IndexFrontmatterStorage,
    IndexingBatchResult,
    IndexInputFile,
    StorageIndexFileWriter,
)
from basic_memory.indexing.note_content_batch_reconciliation import (
    IndexedNoteContentEntity,
    IndexedNoteContentEntityRepository,
    IndexedNoteContentObservedAt,
    IndexedNoteContentReconciler,
    indexed_note_content_observed_at,
    reconcile_indexed_note_content_batch,
)
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconcileFileReader,
    NoteContentReconciler,
)
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository, NoteContentRepository, RelationRepository
from basic_memory.runtime.storage import ProjectId
from basic_memory.services import EntityService


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
    timestamp_provider: IndexedNoteContentObservedAt[FileInfoT] = indexed_note_content_observed_at
    note_content_source: str = "index"
    # Optional canonical-file reader. When set, batch reconciliation re-reads each
    # file at reconcile time instead of trusting the scan snapshot, so a note
    # materialization that rewrote the file between scan and reconcile is not
    # reverted. Local indexing passes a filesystem reader; cloud leaves this None
    # to avoid doubling S3 reads (it can opt in later).
    file_reader: NoteContentReconcileFileReader | None = None

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
            timestamp_provider=self.timestamp_provider,
            max_concurrent=metadata_update_max_concurrent or max_concurrent,
            source=self.note_content_source,
            file_reader=self.file_reader,
        )
        result.errors.extend(error.as_tuple() for error in note_content_errors)
        result.search_indexed = count_search_indexed_entities(result.indexed)
        return result


def count_search_indexed_entities(indexed_entities: list[IndexedEntity]) -> int:
    """Count entities that entered the markdown search path."""
    return sum(1 for indexed in indexed_entities if indexed.markdown_content is not None)


def build_default_index_batch_runtime[FileInfoT: LoadedIndexFile](
    *,
    project_id: ProjectId,
    app_config: BasicMemoryConfig,
    entity_service: EntityService,
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    search_writer: IndexEntitySearchWriter,
    frontmatter_storage: IndexFrontmatterStorage,
    content_type_provider: IndexContentTypeProvider,
    session_maker: async_sessionmaker[AsyncSession],
    file_reader: NoteContentReconcileFileReader | None = None,
) -> IndexBatchRuntime[Entity, FileInfoT]:
    """Compose the default repository-backed batch index runtime.

    Hosted and local runtimes still own storage and session lifecycles. This
    factory keeps the product indexing stack together: search indexing,
    frontmatter writes, batch indexing, and note_content reconciliation.
    """
    note_content_repository = NoteContentRepository(project_id=project_id)
    note_content_reconciler = NoteContentReconciler(
        note_content_repository=note_content_repository,
        session_maker=session_maker,
    )
    # Pass the search writer straight through so the project-scan/batch path
    # search-indexes non-markdown entities (images, PDFs, other files) exactly
    # like the incremental watcher path (LocalMarkdownFileIndexer.index_regular_file).
    # Wrapping it in a markdown-only filter here regressed full/startup scans:
    # non-markdown files were persisted but never added to the search index.
    batch_indexer = BatchIndexer(
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_service=search_writer,
        file_writer=StorageIndexFileWriter(storage=frontmatter_storage),
        session_maker=session_maker,
    )
    return IndexBatchRuntime(
        batch_indexer=batch_indexer,
        content_type_provider=content_type_provider,
        entity_repository=entity_repository,
        session_maker=session_maker,
        note_content_reconciler=note_content_reconciler,
        file_reader=file_reader,
    )
