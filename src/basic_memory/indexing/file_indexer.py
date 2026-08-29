"""Portable per-file markdown indexing service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.file_index_checking import IndexedFileChecksumRow, MoveDetectionEntity
from basic_memory import db
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentReconciliationAnchor,
    NoteContentReconciliationResult,
)
from basic_memory.indexing.models import (
    FileIndexOperation,
    FileIndexResult,
    SyncedMarkdownFile,
)
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    note_content_repository_for_project,
)
from basic_memory.runtime.storage import ProjectId, RuntimeFilePath

if TYPE_CHECKING:  # pragma: no cover
    from loguru._logger import Logger
    from basic_memory.models import Entity


class IndexMarkdownEntity(Protocol):
    """Minimal indexed entity identity needed by runtime adapters."""

    @property
    def id(self) -> int: ...

    @property
    def external_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def checksum(self) -> str | None: ...


class IndexMarkdownEntityRepository(Protocol):
    """Repository capability needed by markdown file indexing adapters."""

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: Path | str,
        *,
        load_relations: bool = True,
    ) -> IndexMarkdownEntity | None: ...

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[Path | str],
    ) -> Sequence[IndexedFileChecksumRow]: ...

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[MoveDetectionEntity]:
        """Return the entities for ``ids`` (move-orphan confirmation)."""


class IndexCurrentMarkdownFileIndexer(Protocol):
    """Capability needed to index one current markdown file from storage."""

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]: ...

    @property
    def entity_repository(self) -> IndexMarkdownEntityRepository: ...

    async def index_current_markdown_file(
        self,
        path: RuntimeFilePath,
        *,
        new: bool,
        index_search: bool,
        resolve_relations: bool,
        refresh_unchanged_derived_state: bool,
    ) -> SyncedMarkdownFile: ...

    async def index_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> FileIndexResult: ...

    async def publish_relation_generation(
        self,
        synced: SyncedMarkdownFile,
        *,
        generation: int,
    ) -> bool:
        """Publish parsed relations only while ``generation`` remains current."""
        ...


class IndexMarkdownNoteContentReconciler(Protocol):
    """Note-content capability needed after canonical markdown sync succeeds."""

    async def capture_anchor(self, entity_id: int | None) -> NoteContentReconciliationAnchor:
        """Capture accepted state before the file observation is indexed."""

    async def reconcile(
        self,
        *,
        entity: Entity,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
        anchor: NoteContentReconciliationAnchor | None = None,
    ) -> NoteContentReconciliationResult: ...


class NoteContentChangedDuringIndexError(RuntimeError):
    """Raised when repeated concurrent writes prevent a current derived-state index."""


MAX_NOTE_CONTENT_INDEX_ATTEMPTS = 2


class FileIndexer:
    """Index one markdown file from the configured project file service."""

    def __init__(
        self,
        *,
        markdown_indexer: IndexCurrentMarkdownFileIndexer,
        note_content_reconciler: IndexMarkdownNoteContentReconciler,
    ) -> None:
        self.markdown_indexer = markdown_indexer
        self.note_content_reconciler = note_content_reconciler

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]:
        """Expose the session owner needed by index-file preflight adapters."""
        return self.markdown_indexer.session_maker

    @property
    def entity_repository(self) -> IndexMarkdownEntityRepository:
        """Expose the entity repository needed by index-file preflight adapters."""
        return self.markdown_indexer.entity_repository

    async def index_file(
        self,
        file_path: str,
        *,
        source: str = "index",
    ) -> FileIndexResult:
        """Index the current file through the configured project index adapter."""
        return await self.markdown_indexer.index_file(file_path, source=source)

    async def index_markdown_file(
        self,
        file_path: str,
        *,
        source: str = "index",
        bound_logger: Logger | None = None,
    ) -> FileIndexResult:
        """Read, parse, persist, search-index, and reconcile one markdown file."""
        log = bound_logger or logger
        log.info("Indexing markdown file: {}", file_path)

        async with db.scoped_session(self.markdown_indexer.session_maker) as session:
            existing = await self.markdown_indexer.entity_repository.get_by_file_path(
                session,
                file_path,
                load_relations=False,
            )
        operation = FileIndexOperation.created if existing is None else FileIndexOperation.updated
        content_superseded = False

        for attempt in range(MAX_NOTE_CONTENT_INDEX_ATTEMPTS):
            if attempt > 0:
                async with db.scoped_session(self.markdown_indexer.session_maker) as session:
                    existing = await self.markdown_indexer.entity_repository.get_by_file_path(
                        session,
                        file_path,
                        load_relations=False,
                    )

            anchor = await self.note_content_reconciler.capture_anchor(
                existing.id if existing is not None else None
            )
            synced = await self.markdown_indexer.index_current_markdown_file(
                file_path,
                new=existing is None,
                index_search=True,
                resolve_relations=False,
                refresh_unchanged_derived_state=existing is not None,
            )

            reconciliation = await self.note_content_reconciler.reconcile(
                entity=synced.entity,
                markdown_content=synced.markdown_content,
                observed_at=synced.updated_at,
                source=source,
                anchor=anchor,
            )
            # Trigger: the indexed file is older than the accepted DB generation.
            # Why: retrying identical bytes cannot make that file lineage current.
            # Outcome: preserve accepted relations and finish without publishing this pass.
            if reconciliation.status == "deferred":
                content_superseded = True
                break

            if reconciliation.generation is not None:
                generation_is_current = await self.markdown_indexer.publish_relation_generation(
                    synced,
                    generation=reconciliation.generation,
                )
                if generation_is_current:
                    break

            log.info(
                "Retrying markdown index without a current relation generation: {}",
                file_path,
                entity_id=synced.entity.id,
                attempt=attempt + 1,
                reconciliation_status=reconciliation.status,
            )
        else:
            # A continuously edited note is normal convergent workload, not a
            # retryable failure. Keep the last coherent snapshot and let the
            # already-coalesced newer write own the next derived-state pass.
            content_superseded = True
            log.info(
                "Finished markdown index with superseded derived state: {}",
                file_path,
                entity_id=synced.entity.id,
            )

        log.info(
            "Indexed markdown file: {}",
            file_path,
            entity_id=synced.entity.id,
            checksum=synced.checksum,
            operation=operation,
            observation_count=len(synced.entity.observations),
            relation_count=len(synced.entity.relations),
        )
        return FileIndexResult.from_fields(
            file_path=file_path,
            entity_id=synced.entity.id,
            external_id=synced.entity.external_id,
            title=synced.entity.title,
            permalink=synced.entity.permalink,
            checksum=synced.checksum,
            operation=operation,
            content_superseded=content_superseded,
        )


def build_default_file_indexer(
    *,
    project_id: ProjectId,
    markdown_indexer: IndexCurrentMarkdownFileIndexer,
) -> FileIndexer:
    """Compose the default repository-backed per-file markdown indexer."""
    return FileIndexer(
        markdown_indexer=markdown_indexer,
        note_content_reconciler=NoteContentReconciler(
            note_content_repository=note_content_repository_for_project(project_id),
            session_maker=markdown_indexer.session_maker,
        ),
    )
