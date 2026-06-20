"""Portable per-file markdown indexing service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import (
    FileIndexOperation,
    FileIndexResult,
    SyncedMarkdownFile,
)
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    note_content_repository_for_project,
)
from basic_memory.runtime import ProjectId, RuntimeFilePath

if TYPE_CHECKING:  # pragma: no cover
    from loguru._logger import Logger
    from basic_memory.models import Entity


class IndexMarkdownEntity(Protocol):
    """Minimal indexed entity identity needed by runtime adapters."""

    @property
    def id(self) -> int: ...

    @property
    def external_id(self) -> object | None: ...

    @property
    def title(self) -> object | None: ...

    @property
    def permalink(self) -> object | None: ...

    @property
    def checksum(self) -> object | None: ...


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
    ) -> Sequence[Row[Any]]: ...


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


class IndexMarkdownNoteContentReconciler(Protocol):
    """Note-content capability needed after canonical markdown sync succeeds."""

    async def reconcile(
        self,
        *,
        entity: Entity,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
    ) -> None: ...


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

    async def index_markdown_file(
        self,
        file_path: str,
        *,
        source: str = "index",
        bound_logger: Logger | None = None,
    ) -> FileIndexResult:
        """Read, parse, persist, search-index, and reconcile one markdown file."""
        log = bound_logger or logger
        log.info(f"Indexing markdown file: {file_path}")

        async with db.scoped_session(self.markdown_indexer.session_maker) as session:
            existing = await self.markdown_indexer.entity_repository.get_by_file_path(
                session,
                file_path,
                load_relations=False,
            )
        operation = FileIndexOperation.created if existing is None else FileIndexOperation.updated

        synced = await self.markdown_indexer.index_current_markdown_file(
            file_path,
            new=existing is None,
            index_search=True,
            resolve_relations=False,
            refresh_unchanged_derived_state=existing is not None,
        )

        await self.note_content_reconciler.reconcile(
            entity=synced.entity,
            markdown_content=synced.markdown_content,
            observed_at=synced.updated_at,
            source=source,
        )

        log.info(
            f"Indexed markdown file: {file_path}",
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
