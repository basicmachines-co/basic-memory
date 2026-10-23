"""Apply note-content reconciliation plans through the database repository."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Protocol, assert_never

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing.note_content_reconciliation import (
    AcceptedNoteContentLineage,
    NoteContentBootstrap,
    NoteContentChecksum,
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
from basic_memory.repository.note_content_repository import AcceptedNoteContentVersions
from basic_memory.runtime.note_file_guards import (
    RuntimeFileChecksumReader,
    read_runtime_file_checksum,
)
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

    async def find_accepted_content_versions(
        self,
        session: AsyncSession,
        entity_id: int,
        checksum: NoteContentChecksum,
    ) -> AcceptedNoteContentVersions:
        """Locate one checksum in the note's accepted-change history."""


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


class PromotionConfirmationStorage(RuntimeFileChecksumReader, Protocol):
    """File storage that says whether it is the host's local filesystem."""

    @property
    def local_project_path(self) -> Path | None: ...


def local_promotion_confirmation_reader(
    storage: PromotionConfirmationStorage,
) -> RuntimeFileChecksumReader | None:
    """Return ``storage`` as the authoritative re-read only for a local filesystem.

    Constraint: object-storage file services (Cloud's S3 service subclasses
    FileService) compute checksums with ordinary reads, which can serve the same
    stale object generation the promotion rollback guard exists to catch. Only a
    local filesystem re-read is authoritative, so only it may confirm a
    superseded observation. Object storage gets None, and the guard defers.
    """
    return storage if storage.local_project_path is not None else None


# --- Promotion Rollback Guard ---

# How the rollback guard judged one planned promotion:
# - not_superseded: observed bytes match no accepted version, or the newest one
# - confirmed_by_storage: superseded bytes, but an authoritative re-read agrees
# - no_authoritative_reader: superseded bytes and nothing can confirm them
# - storage_disagrees: superseded bytes and the authoritative re-read differs
type NoteContentRollbackGuardOutcome = Literal[
    "not_superseded",
    "confirmed_by_storage",
    "no_authoritative_reader",
    "storage_disagrees",
]


def rollback_guard_allows_promotion(outcome: NoteContentRollbackGuardOutcome) -> bool:
    """Return whether a planned promotion may be applied after the rollback guard."""
    return outcome in ("not_superseded", "confirmed_by_storage")


async def guard_note_content_promotion(
    lineage: AcceptedNoteContentLineage,
    *,
    confirmation_reader: RuntimeFileChecksumReader | None,
    file_path: RuntimeFilePath,
    observed_checksum: NoteContentChecksum,
) -> NoteContentRollbackGuardOutcome:
    """Decide whether observed bytes that would become a new version are trustworthy.

    Trigger: the observed checksum equals an accepted version that a newer accepted
      change already replaced.
    Why: that is the signature of a stale object-storage read. Promoting it would save
      old bytes as the newest version and silently roll the note back.
    Outcome: promote only when an authoritative storage re-read returns the same
      bytes; otherwise the caller defers and the DB keeps the newer accepted content.

    Trade-off: a sync user who deliberately restores a file to the exact bytes of an
    older accepted version is deferred too, unless the authoritative re-read confirms
    it. Nothing is lost: the newer content stays accepted in the DB.
    """
    if not lineage.observation_is_superseded:
        return "not_superseded"
    if confirmation_reader is None:
        return "no_authoritative_reader"
    confirmed_checksum = await read_runtime_file_checksum(confirmation_reader, file_path)
    if confirmed_checksum == observed_checksum:
        return "confirmed_by_storage"
    return "storage_disagrees"


@dataclass(frozen=True, slots=True)
class NoteContentPromotionRecord:
    """Structured telemetry for one applied or refused note_content promotion."""

    entity_id: int
    previous_db_version: int
    new_db_version: int | None
    observed_checksum: NoteContentChecksum
    matched_accepted_db_version: int | None
    newest_accepted_db_version: int | None
    observed_at: str
    source: NoteContentSource
    rollback_guard: NoteContentRollbackGuardOutcome


def log_note_content_promotion(record: NoteContentPromotionRecord) -> None:
    """Emit one promotion decision so silent version changes leave a trace.

    Applied promotions log at info. Refused rollback candidates log at warning,
    because each one is a storage read that tried to roll a note back.
    """
    fields = asdict(record)
    if record.new_db_version is None:
        logger.warning(
            "Refused note_content promotion: observed file matches a superseded accepted version",
            event="note_content.promotion_refused",
            **fields,
        )
        return
    logger.info(
        "Promoted observed file content to a new accepted note_content version",
        event="note_content.promotion_applied",
        **fields,
    )


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
        promotion_confirmation_reader: RuntimeFileChecksumReader | None = None,
    ) -> None:
        self._note_content_repository = note_content_repository
        self._session_maker = session_maker
        # Authoritative storage re-read for promotions of superseded bytes. None
        # means storage reads cannot be trusted to confirm them, so they defer.
        self._promotion_confirmation_reader = promotion_confirmation_reader

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

            # Trigger: the planner wants the observed bytes to become a new accepted version.
            # Why: a promotion records no accepted_project_note_change row and no live
            #   update, so a stale storage read here would silently roll the note back.
            # Outcome: superseded bytes need authoritative confirmation or they defer;
            #   every applied or refused promotion leaves a structured log record.
            applied_promotion_record: NoteContentPromotionRecord | None = None
            if isinstance(plan, NoteContentPromoted):
                accepted_versions = (
                    await self._note_content_repository.find_accepted_content_versions(
                        session,
                        entity.id,
                        observed.checksum,
                    )
                )
                lineage = AcceptedNoteContentLineage(
                    newest_accepted_db_version=accepted_versions.newest_db_version,
                    matched_accepted_db_version=accepted_versions.matched_db_version,
                )
                # note_content mirrors the entity path; a stale mirror can only make
                # the re-read disagree, which defers rather than promotes.
                guard_outcome = await guard_note_content_promotion(
                    lineage,
                    confirmation_reader=self._promotion_confirmation_reader,
                    file_path=note_content.file_path,
                    observed_checksum=observed.checksum,
                )
                promotion_record = NoteContentPromotionRecord(
                    entity_id=entity.id,
                    previous_db_version=current_state.db_version,
                    new_db_version=None,
                    observed_checksum=observed.checksum,
                    matched_accepted_db_version=lineage.matched_accepted_db_version,
                    newest_accepted_db_version=lineage.newest_accepted_db_version,
                    observed_at=observed.observed_at.isoformat(),
                    source=observed.source,
                    rollback_guard=guard_outcome,
                )
                if not rollback_guard_allows_promotion(guard_outcome):
                    log_note_content_promotion(promotion_record)
                    return NoteContentReconciliationResult.deferred()
                applied_promotion_record = replace(promotion_record, new_db_version=plan.db_version)

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

            if applied_promotion_record is not None:
                log_note_content_promotion(applied_promotion_record)

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
