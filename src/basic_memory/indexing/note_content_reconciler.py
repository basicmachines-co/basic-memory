"""Apply note-content reconciliation plans through the database repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, assert_never

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentBootstrap,
    NoteContentFileObserved,
    NoteContentFileSynced,
    NoteContentPromoted,
    NoteContentState,
    ObservedNoteContent,
    plan_note_content_reconciliation,
)
from basic_memory.models import Entity, NoteContent

type NoteContentUpdatePlan = NoteContentFileSynced | NoteContentFileObserved | NoteContentPromoted


class NoteContentStore(Protocol):
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

    async def update_state_fields(
        self,
        session: AsyncSession,
        entity_id: int,
        **updates: object,
    ) -> NoteContent | None:
        """Update mutable note_content state fields."""


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
    repository: NoteContentStore,
    session: AsyncSession,
    entity_id: int,
    plan: NoteContentUpdatePlan,
) -> None:
    """Apply a non-bootstrap reconciliation decision to the note_content repository."""
    match plan:
        case NoteContentFileSynced():
            await repository.update_state_fields(
                session,
                entity_id,
                markdown_content=plan.markdown_content,
                file_version=plan.file_version,
                file_checksum=plan.file_checksum,
                file_write_status=plan.file_write_status,
                file_updated_at=plan.file_updated_at,
                last_materialization_error=plan.last_materialization_error,
                last_materialization_attempt_at=plan.last_materialization_attempt_at,
            )
        case NoteContentFileObserved():
            await repository.update_state_fields(
                session,
                entity_id,
                file_version=plan.file_version,
                file_checksum=plan.file_checksum,
                file_updated_at=plan.file_updated_at,
            )
        case NoteContentPromoted():
            await repository.update_state_fields(
                session,
                entity_id,
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

    async def reconcile(
        self,
        *,
        entity: Entity,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
    ) -> None:
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

            if note_content is None:
                plan = plan_note_content_reconciliation(None, observed)
                if not isinstance(plan, NoteContentBootstrap):
                    raise RuntimeError("Missing note_content must bootstrap reconciliation")

                try:
                    await self._note_content_repository.create(
                        session,
                        note_content_from_bootstrap(entity.id, plan),
                    )
                    return
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

            plan = plan_note_content_reconciliation(
                note_content_state_from_model(note_content),
                observed,
            )
            if isinstance(plan, NoteContentBootstrap):
                raise RuntimeError("Existing note_content cannot bootstrap reconciliation")

            await apply_note_content_update_plan(
                self._note_content_repository,
                session,
                entity.id,
                plan,
            )
