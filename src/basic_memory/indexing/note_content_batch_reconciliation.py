"""Portable batch reconciliation for indexed markdown note_content rows."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from loguru import logger
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.file_index_planning import FileIndexPath
from basic_memory.indexing.models import IndexedEntity


class IndexedNoteContentEntity(Protocol):
    """Minimal entity identity needed after a batch index write."""

    id: int


class IndexedNoteContentFileInfo(Protocol):
    """Minimal loaded-file state needed to timestamp indexed note_content."""

    @property
    def checksum(self) -> str | None: ...

    @property
    def last_modified(self) -> datetime | None: ...


EntityT = TypeVar("EntityT", bound=IndexedNoteContentEntity)


class IndexingTask[ResultT](Protocol):
    """Retryable indexing follow-up task."""

    async def run(self) -> ResultT:
        """Execute one fresh task attempt."""


class IndexedNoteContentTimestampProvider[FileInfoT: IndexedNoteContentFileInfo](Protocol):
    """Timestamp provider for indexed note_content reconciliation."""

    def observed_at(
        self,
        indexed: IndexedEntity,
        file_info: FileInfoT | None,
    ) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class DefaultIndexedNoteContentTimestampProvider:
    """Default timestamp provider for indexed markdown note_content."""

    def observed_at(
        self,
        indexed: IndexedEntity,
        file_info: IndexedNoteContentFileInfo | None,
    ) -> datetime | None:
        return indexed_note_content_observed_at(indexed, file_info)


class IndexedNoteContentEntityRepository(Protocol[EntityT]):
    """Repository capability needed to reload indexed markdown entities."""

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[EntityT]:
        """Load indexed entities by primary key."""


class IndexedNoteContentReconciler(Protocol[EntityT]):
    """Capability that reconciles one indexed markdown entity into note_content."""

    async def reconcile(
        self,
        *,
        entity: EntityT,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
    ) -> None:
        """Apply the note_content state change for one markdown entity."""


@dataclass(frozen=True, slots=True)
class IndexedNoteContentReconciliationError:
    """Per-file note_content reconciliation failure captured after indexing."""

    path: FileIndexPath
    message: str

    def as_tuple(self) -> tuple[FileIndexPath, str]:
        """Return the existing IndexingBatchResult error tuple shape."""
        return self.path, self.message


def indexed_note_content_utc_now() -> datetime:
    """Return the current UTC time used to stamp rewritten indexed observations."""
    return datetime.now(tz=UTC)


def indexed_note_content_observed_at(
    indexed: IndexedEntity,
    file_info: IndexedNoteContentFileInfo | None,
) -> datetime | None:
    """Choose the file timestamp that matches an indexed markdown version."""
    if file_info is None:
        return None

    if indexed.checksum != file_info.checksum:
        # Frontmatter rewrites create a new storage object during indexing, so the
        # original file timestamp no longer describes the indexed markdown version.
        return indexed_note_content_utc_now()

    return file_info.last_modified


async def run_indexing_tasks_with_retries[ResultT](
    tasks: Sequence[IndexingTask[ResultT]],
    *,
    max_concurrent: int,
    max_attempts: int = 3,
    retry_wait_seconds: float = 1.0,
) -> tuple[ResultT | BaseException, ...]:
    """Run indexing task factories with bounded concurrency and SQL timeout retries."""
    if max_concurrent <= 0:
        raise ValueError("max_concurrent must be greater than zero")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    if retry_wait_seconds < 0:
        raise ValueError("retry_wait_seconds must be non-negative")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_one(task: IndexingTask[ResultT]) -> ResultT:
        async with semaphore:
            wait_seconds = retry_wait_seconds
            for attempt in range(1, max_attempts + 1):
                try:
                    return await task.run()
                except SQLAlchemyTimeoutError:
                    if attempt == max_attempts:
                        raise
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    wait_seconds = min(wait_seconds * 2, 10.0)

        raise RuntimeError("indexing task retry loop exited unexpectedly")

    results = await asyncio.gather(
        *(run_one(task) for task in tasks),
        return_exceptions=True,
    )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class IndexedNoteContentReconciliationTask[
    EntityT: IndexedNoteContentEntity,
    FileInfoT: IndexedNoteContentFileInfo,
]:
    """Reconcile one indexed markdown entity into note_content."""

    indexed: IndexedEntity
    entity_by_id: Mapping[int, EntityT]
    file_infos: Mapping[FileIndexPath, FileInfoT]
    note_content_reconciler: IndexedNoteContentReconciler[EntityT]
    timestamp_provider: IndexedNoteContentTimestampProvider[FileInfoT]
    source: str

    async def run(self) -> IndexedNoteContentReconciliationError | None:
        if self.indexed.markdown_content is None:
            return None

        entity = self.entity_by_id.get(self.indexed.entity_id)
        if entity is None:
            return IndexedNoteContentReconciliationError(
                self.indexed.path,
                f"Entity {self.indexed.entity_id} not found after indexing",
            )

        try:
            await self.note_content_reconciler.reconcile(
                entity=entity,
                markdown_content=self.indexed.markdown_content,
                observed_at=self.timestamp_provider.observed_at(
                    self.indexed,
                    self.file_infos.get(self.indexed.path),
                ),
                source=self.source,
            )
            return None
        except Exception as exc:  # pragma: no cover - defensive logging
            # The entity/search writes are already durable by this point. Report
            # the note_content follow-up failure as a per-file indexing error.
            logger.error(f"Failed to reconcile note_content for {self.indexed.path}: {exc}")
            return IndexedNoteContentReconciliationError(self.indexed.path, str(exc))


async def reconcile_indexed_note_content_batch[
    EntityT: IndexedNoteContentEntity,
    FileInfoT: IndexedNoteContentFileInfo,
](
    indexed_entities: Sequence[IndexedEntity],
    *,
    file_infos: Mapping[FileIndexPath, FileInfoT],
    entity_repository: IndexedNoteContentEntityRepository[EntityT],
    session_maker: async_sessionmaker[AsyncSession],
    note_content_reconciler: IndexedNoteContentReconciler[EntityT],
    timestamp_provider: IndexedNoteContentTimestampProvider[FileInfoT],
    max_concurrent: int,
    source: str = "index",
) -> tuple[IndexedNoteContentReconciliationError, ...]:
    """Hydrate note_content rows for indexed markdown entities after batch indexing."""
    markdown_entities = tuple(
        indexed for indexed in indexed_entities if indexed.markdown_content is not None
    )
    if not markdown_entities:
        return ()

    async with db.scoped_session(session_maker) as session:
        stored_entities = await entity_repository.find_by_ids(
            session,
            [indexed.entity_id for indexed in markdown_entities],
        )
    entity_by_id = {entity.id: entity for entity in stored_entities}

    results = await run_indexing_tasks_with_retries(
        [
            IndexedNoteContentReconciliationTask(
                indexed=indexed,
                entity_by_id=entity_by_id,
                file_infos=file_infos,
                note_content_reconciler=note_content_reconciler,
                timestamp_provider=timestamp_provider,
                source=source,
            )
            for indexed in markdown_entities
        ],
        max_concurrent=max_concurrent,
    )

    errors: list[IndexedNoteContentReconciliationError] = []
    for indexed, result in zip(markdown_entities, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(IndexedNoteContentReconciliationError(indexed.path, str(result)))
        elif result is not None:
            errors.append(result)
    return tuple(errors)
