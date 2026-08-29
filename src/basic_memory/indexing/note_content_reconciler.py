"""Apply note-content reconciliation plans through the database repository."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Protocol, assert_never

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentBootstrap,
    NoteContentFileObserved,
    NoteContentFileSynced,
    NoteContentMaterializationStatusUpdate,
    NoteContentMaterializedCurrent,
    NoteContentMaterializedStale,
    NoteContentPromoted,
    NoteContentReconciliationAnchor,
    NoteContentReconciliationDeferred,
    NoteContentReconciliationResult,
    NoteContentState,
    NoteContentSource,
    NoteContentWriteStatus,
    ObservedNoteContent,
    bootstrap_note_content_plan,
    plan_existing_note_content_reconciliation,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.repository import NoteContentRepository
from basic_memory.runtime.storage import ProjectId, RuntimeEntityId, RuntimeFilePath

type NoteContentUpdatePlan = (
    NoteContentFileSynced
    | NoteContentFileObserved
    | NoteContentMaterializedCurrent
    | NoteContentMaterializedStale
    | NoteContentMaterializationStatusUpdate
    | NoteContentPromoted
)


class NoteContentStateUpdateStore(Protocol):
    """Repository capability needed to update note_content state."""

    async def update_state_fields(
        self,
        session: AsyncSession,
        entity_id: int,
        *,
        expected_db_version: int | None = None,
        **updates: object,
    ) -> NoteContent | None:
        """Update mutable note_content state fields, optionally version-guarded."""


class NoteContentStore(NoteContentStateUpdateStore, Protocol):
    """Repository capability needed by note-content reconciliation."""

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> NoteContent | None:
        """Load note_content by owning entity id."""

    async def create(
        self,
        session: AsyncSession,
        data: NoteContent,
    ) -> NoteContent:
        """Create a note_content row."""


class NoteContentReconcileEntitySource(Protocol):
    """Minimal entity shape needed by note-content reconciliation."""

    @property
    def id(self) -> RuntimeEntityId: ...


class NoteContentReconcileFileEntitySource(NoteContentReconcileEntitySource, Protocol):
    """Entity shape needed to reconcile from the canonical file."""

    @property
    def file_path(self) -> RuntimeFilePath: ...

    @property
    def is_markdown(self) -> bool: ...


class NoteContentReconcileEntityRepository(Protocol):
    """Repository capability for loading the entity to reconcile."""

    async def find_by_id(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> NoteContentReconcileFileEntitySource | None:
        """Load one entity by internal id."""


class NoteContentReconcileFile(Protocol):
    """Canonical storage file content used for note-content reconciliation."""

    @property
    def content(self) -> bytes | None: ...

    @property
    def last_modified(self) -> datetime | None: ...


class NoteContentReconcileFileReader(Protocol):
    """Storage capability for reading one canonical entity file."""

    async def get_file(self, path: RuntimeFilePath) -> NoteContentReconcileFile:
        """Return canonical file content for one relative path."""


def note_content_repository_for_project(project_id: ProjectId) -> NoteContentStore:
    """Build the default note_content repository for one project."""
    return NoteContentRepository(project_id=project_id)


# Injected per-project store seam for materialization bookkeeping; the default
# is note_content_repository_for_project above.
type NoteContentStoreFactory = Callable[[ProjectId], NoteContentStateUpdateStore]


@dataclass(frozen=True, slots=True)
class RepositoryNoteMaterializationFailureMarker:
    """Repository-backed failure marker for accepted-note materialization enqueue failures."""

    session_maker: async_sessionmaker[AsyncSession]
    note_content_store: NoteContentStoreFactory = note_content_repository_for_project

    async def mark_note_materialization_failed(
        self,
        *,
        project_id: ProjectId,
        entity_id: RuntimeEntityId,
        error_message: str,
    ) -> None:
        """Mark accepted note content as failed when queue submission cannot start."""
        async with self.session_maker() as session:
            async with session.begin():
                await self.note_content_store(project_id).update_state_fields(
                    session,
                    entity_id,
                    file_write_status="failed",
                    last_materialization_error=error_message,
                    last_materialization_attempt_at=datetime.now(tz=UTC),
                )


def note_content_write_status(value: str) -> NoteContentWriteStatus:
    """Validate the persisted write status before using it in pure planning."""
    match value:
        case "pending" | "writing" | "synced" | "failed" | "external_change_detected":
            return value
        case _:
            raise ValueError(f"Unsupported note_content file_write_status: {value}")


def note_content_state_from_model(note_content: NoteContent) -> NoteContentState:
    """Map the ORM row into the portable reconciliation state."""
    return NoteContentState(
        db_version=int(note_content.db_version),
        db_checksum=str(note_content.db_checksum),
        file_version=int(note_content.file_version)
        if note_content.file_version is not None
        else None,
        file_checksum=str(note_content.file_checksum)
        if note_content.file_checksum is not None
        else None,
        file_write_status=note_content_write_status(note_content.file_write_status),
    )


def note_content_from_bootstrap(entity_id: int, plan: NoteContentBootstrap) -> NoteContent:
    """Build a note_content row from a portable bootstrap decision."""
    return NoteContent(
        entity_id=entity_id,
        markdown_content=plan.markdown_content,
        db_version=plan.db_version,
        db_checksum=plan.db_checksum,
        file_version=plan.file_version,
        file_checksum=plan.file_checksum,
        file_write_status=plan.file_write_status,
        last_source=plan.last_source,
        updated_at=plan.updated_at,
        file_updated_at=plan.file_updated_at,
        last_materialization_error=plan.last_materialization_error,
        last_materialization_attempt_at=plan.last_materialization_attempt_at,
    )


async def apply_note_content_update_plan(
    repository: NoteContentStateUpdateStore,
    session: AsyncSession,
    entity_id: int,
    plan: NoteContentUpdatePlan,
    *,
    expected_db_version: int | None = None,
) -> bool:
    """Apply a non-bootstrap reconciliation decision to the note_content repository.

    The plan was computed from note_content read at ``expected_db_version``; the
    version-guarded write applies it only while the row is still at that version.
    Returns False when a concurrent accepted write advanced the row first, so the
    stale plan is skipped rather than reverting the newer content.
    """
    match plan:
        case NoteContentFileSynced():
            updated = await repository.update_state_fields(
                session,
                entity_id,
                expected_db_version=expected_db_version,
                markdown_content=plan.markdown_content,
                file_version=plan.file_version,
                file_checksum=plan.file_checksum,
                file_write_status=plan.file_write_status,
                file_updated_at=plan.file_updated_at,
                last_materialization_error=plan.last_materialization_error,
                last_materialization_attempt_at=plan.last_materialization_attempt_at,
            )
        case NoteContentFileObserved():
            updated = await repository.update_state_fields(
                session,
                entity_id,
                expected_db_version=expected_db_version,
                file_version=plan.file_version,
                file_checksum=plan.file_checksum,
                file_updated_at=plan.file_updated_at,
            )
        case NoteContentMaterializedCurrent() | NoteContentMaterializedStale():
            updated = await repository.update_state_fields(
                session,
                entity_id,
                expected_db_version=expected_db_version,
                file_version=plan.file_version,
                file_checksum=plan.file_checksum,
                file_write_status=plan.file_write_status,
                file_updated_at=plan.file_updated_at,
                last_materialization_error=plan.last_materialization_error,
                last_materialization_attempt_at=plan.last_materialization_attempt_at,
            )
        case NoteContentMaterializationStatusUpdate():
            updates: dict[str, object] = {
                "file_write_status": plan.file_write_status,
                "last_materialization_error": plan.last_materialization_error,
                "last_materialization_attempt_at": plan.last_materialization_attempt_at,
            }
            if plan.file_checksum is not None:
                updates["file_checksum"] = plan.file_checksum
            updated = await repository.update_state_fields(
                session, entity_id, expected_db_version=expected_db_version, **updates
            )
        case NoteContentPromoted():
            updated = await repository.update_state_fields(
                session,
                entity_id,
                expected_db_version=expected_db_version,
                markdown_content=plan.markdown_content,
                db_version=plan.db_version,
                db_checksum=plan.db_checksum,
                file_version=plan.file_version,
                file_checksum=plan.file_checksum,
                file_write_status=plan.file_write_status,
                last_source=plan.last_source,
                updated_at=plan.updated_at,
                file_updated_at=plan.file_updated_at,
                last_materialization_error=plan.last_materialization_error,
                last_materialization_attempt_at=plan.last_materialization_attempt_at,
            )
        case _:
            assert_never(plan)

    return updated is not None


class NoteContentReconciler:
    """Keep note_content aligned with one observed markdown file version."""

    def __init__(
        self,
        *,
        note_content_repository: NoteContentStore,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._note_content_repository = note_content_repository
        self._session_maker = session_maker

    async def capture_anchor(self, entity_id: int | None) -> NoteContentReconciliationAnchor:
        """Capture the accepted state before storage bytes enter the index pipeline."""
        if entity_id is None:
            return NoteContentReconciliationAnchor(entity_id=None, state=None)

        async with db.scoped_session(self._session_maker) as session:
            note_content = await self._note_content_repository.get_by_entity_id(
                session,
                entity_id,
            )
        return NoteContentReconciliationAnchor(
            entity_id=entity_id,
            state=note_content_state_from_model(note_content) if note_content else None,
        )

    async def reconcile(
        self,
        *,
        entity: NoteContentReconcileEntitySource,
        markdown_content: str,
        observed_at: datetime | None,
        source: NoteContentSource,
        anchor: NoteContentReconciliationAnchor | None = None,
    ) -> NoteContentReconciliationResult:
        """Apply the shared file-vs-DB rule for one markdown entity."""
        observed_checksum = await file_utils.compute_checksum(markdown_content)
        observed_timestamp = observed_at or datetime.now(tz=UTC)
        observed = ObservedNoteContent(
            markdown_content=markdown_content,
            checksum=observed_checksum,
            observed_at=observed_timestamp,
            source=source,
        )
        async with db.scoped_session(self._session_maker) as session:
            note_content = await self._note_content_repository.get_by_entity_id(
                session,
                entity.id,
            )

            current_state = (
                note_content_state_from_model(note_content) if note_content is not None else None
            )
            if anchor is not None:
                if anchor.entity_id is not None and anchor.entity_id != entity.id:
                    raise ValueError(
                        "Note-content reconciliation anchor belongs to another entity."
                    )
                if anchor.state != current_state:
                    logger.debug(
                        "Skipped stale note_content observation for entity {}: "
                        "accepted state changed during indexing",
                        entity.id,
                    )
                    return NoteContentReconciliationResult.stale()

            if note_content is None:
                # Missing NoteContent cannot itself fence a bootstrap. Lock and
                # revalidate the Entity so a resource reclassification that won
                # the race cannot receive canonical Markdown state afterward.
                locked_entity = await session.get(
                    Entity,
                    entity.id,
                    with_for_update=True,
                )
                if locked_entity is None or not locked_entity.is_markdown:
                    logger.debug(
                        "Skipped note_content bootstrap for non-Markdown entity {}",
                        entity.id,
                    )
                    return NoteContentReconciliationResult.stale()
                bootstrap = bootstrap_note_content_plan(observed=observed)

                try:
                    await self._note_content_repository.create(
                        session,
                        note_content_from_bootstrap(entity.id, bootstrap),
                    )
                    return NoteContentReconciliationResult.current(bootstrap.db_version)
                except IntegrityError:
                    # Concurrent repair/index workers can both observe a missing row before
                    # one wins the insert. Reload the winner and let normal reconciliation
                    # converge this observed file instead of failing the job.
                    await session.rollback()
                    note_content = await self._note_content_repository.get_by_entity_id(
                        session,
                        entity.id,
                    )
                    if note_content is None:
                        raise
                    if anchor is not None and anchor.state != note_content_state_from_model(
                        note_content
                    ):
                        logger.debug(
                            "Skipped stale note_content bootstrap for entity {}: "
                            "accepted state was created during indexing",
                            entity.id,
                        )
                        return NoteContentReconciliationResult.stale()

            # The plan is computed from the row read above; guard the write on
            # that db_version so a concurrent accepted API mutation that advanced
            # the row between our read and write is not silently reverted.
            expected_db_version = int(note_content.db_version)
            current_state = note_content_state_from_model(note_content)
            plan = plan_existing_note_content_reconciliation(
                current=current_state,
                observed=observed,
            )
            if isinstance(plan, NoteContentReconciliationDeferred):
                logger.debug(
                    "Deferred note_content file promotion for entity {}: "
                    "db_version={}, file_version={}, file_write_status={}",
                    entity.id,
                    current_state.db_version,
                    current_state.file_version,
                    current_state.file_write_status,
                )
                return NoteContentReconciliationResult.deferred()

            applied = await apply_note_content_update_plan(
                self._note_content_repository,
                session,
                entity.id,
                plan,
                expected_db_version=expected_db_version,
            )
            if not applied:
                logger.debug(
                    "Skipped stale note_content reconcile: a concurrent accepted "
                    "write advanced db_version {} for entity {}",
                    expected_db_version,
                    entity.id,
                )
                return NoteContentReconciliationResult.stale()

            if isinstance(plan, NoteContentFileObserved):
                # The observed bytes describe an older materialized file while DB content is
                # already ahead. They may update file bookkeeping, but they cannot publish
                # semantic relations under the newer accepted generation.
                return NoteContentReconciliationResult.deferred()

            claimed_generation = (
                plan.db_version if isinstance(plan, NoteContentPromoted) else expected_db_version
            )
            return NoteContentReconciliationResult.current(claimed_generation)


async def reconcile_note_content_for_entity(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    entity_repository: NoteContentReconcileEntityRepository,
    file_reader: NoteContentReconcileFileReader,
    reconciler: NoteContentReconciler,
    entity_id: RuntimeEntityId,
    source: NoteContentSource,
) -> bool:
    """Hydrate or refresh note_content from an entity's canonical markdown file."""
    async with session_maker() as session:
        entity = await entity_repository.find_by_id(session, entity_id)
    if entity is None or not entity.is_markdown:
        return False

    file_info = await file_reader.get_file(entity.file_path)
    if file_info.content is None:
        raise ValueError(f"Missing markdown content for entity {entity_id}")

    await reconciler.reconcile(
        entity=entity,
        markdown_content=file_info.content.decode("utf-8"),
        observed_at=file_info.last_modified,
        source=source,
    )
    return True
