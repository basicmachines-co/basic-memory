"""Repository-backed read handoffs for accepted note_content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.note_content_reconciler import NoteContentReconciler
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import EntityRepository, NoteContentRepository, ProjectRepository
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentReadAction,
    RuntimeNoteContentReadRepairStatus,
    RuntimeNoteContentResource,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteContentState,
    plan_runtime_note_content_read,
    plan_runtime_note_content_read_repair,
)
from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectExternalId,
    RuntimeNoteChangeSource,
    runtime_content_type_is_markdown,
)
from basic_memory.schemas.v2.entity import EntityResponseV2

# --- Read/repair value objects ---


@dataclass(frozen=True, slots=True)
class NoteContentReadView[EntityT, NoteContentT]:
    """Joined entity plus accepted note_content used by hot note reads."""

    entity: EntityT
    note_content: NoteContentT | None


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairTarget[ProjectT, EntityT]:
    """Storage object identity needed after DB preflight allows read repair."""

    project: ProjectT
    entity: EntityT


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairFile:
    """Canonical markdown content observed by a read-repair storage adapter."""

    markdown_content: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairPreflight[ProjectT, EntityT]:
    """DB preflight result for a note-content read-repair attempt."""

    status: RuntimeNoteContentReadRepairStatus
    target: NoteContentReadRepairTarget[ProjectT, EntityT] | None = None

    @property
    def should_read_file(self) -> bool:
        """Return whether the caller should read the canonical storage object."""
        return self.status is RuntimeNoteContentReadRepairStatus.read_file

    @property
    def repaired(self) -> bool:
        """Return whether note_content is already usable after DB preflight."""
        return self.status is RuntimeNoteContentReadRepairStatus.already_present

    def require_target(self) -> NoteContentReadRepairTarget[ProjectT, EntityT]:
        """Return the storage read target for a repair that must read a file."""
        if self.target is None:
            raise RuntimeError("note-content read repair preflight does not contain a target")
        return self.target


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairRun:
    """Typed outcome for a complete note-content read-repair attempt."""

    status: RuntimeNoteContentReadRepairStatus

    @property
    def repaired(self) -> bool:
        """Return whether note_content is usable after the repair attempt."""
        return self.status in {
            RuntimeNoteContentReadRepairStatus.already_present,
            RuntimeNoteContentReadRepairStatus.repaired,
        }


class NoteContentReadRepairFileReader[ProjectT, EntityT](Protocol):
    """Capability that reads the canonical markdown file for read repair.

    This is the real storage seam: local runtimes read from the project
    filesystem while hosted runtimes read from object storage.
    """

    async def read_note_content_repair_file(
        self,
        target: NoteContentReadRepairTarget[ProjectT, EntityT],
    ) -> NoteContentReadRepairFile | None: ...


# --- Hot note-content reads ---


async def load_note_content_read_view_with_default_repositories(
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
) -> NoteContentReadView[Entity, NoteContent] | None:
    """Load the hot read view through the default Basic Memory repositories."""
    project = await ProjectRepository().get_by_external_id(session, project_external_id)
    if project is None:
        return None

    entity = await EntityRepository(project_id=project.id).get_by_external_id(
        session,
        entity_external_id,
    )
    if entity is None:
        return None

    note_content = None
    if runtime_content_type_is_markdown(entity):
        note_content = await NoteContentRepository(project_id=project.id).get_by_entity_id(
            session,
            entity.id,
        )

    return NoteContentReadView(entity=entity, note_content=note_content)


# EntityResponseV2 also carries the accepted note_content bookkeeping columns
# (versions, checksums, write status). Metadata-only note-content reads omit them
# so route payloads do not leak DB-internal write state; a test pins every name
# here to a real EntityResponseV2 field so the set cannot drift silently.
ENTITY_METADATA_PAYLOAD_EXCLUDE: frozenset[str] = frozenset(
    {
        "db_version",
        "db_checksum",
        "file_version",
        "file_checksum",
        "file_write_status",
        "last_source",
        "file_updated_at",
        "last_materialization_error",
        "sync_error",
    }
)


def entity_metadata_response_payload(entity: Entity) -> RuntimeNoteContentResponsePayload:
    """Serialize the metadata-only payload for a non-accepted note-content read."""
    return EntityResponseV2.model_validate(entity).model_dump(
        mode="json",
        exclude=set(ENTITY_METADATA_PAYLOAD_EXCLUDE),
    )


def note_content_response_payload_from_read_view(
    view: NoteContentReadView[Entity, NoteContent] | None,
) -> RuntimeNoteContentResponsePayload | None:
    """Build the typed response payload for a loaded note-content read view."""
    if view is None:
        return None

    read_plan = plan_runtime_note_content_read(view.entity, view.note_content)
    if read_plan.action is RuntimeNoteContentReadAction.entity_metadata:
        return entity_metadata_response_payload(read_plan.require_entity_metadata())

    if read_plan.action is not RuntimeNoteContentReadAction.accepted_note:
        return None

    entity, note_content = read_plan.require_accepted_note()
    return RuntimeAcceptedNoteResponse.from_entity_and_content_state(
        entity=entity,
        note_content=RuntimeNoteContentState.from_source(note_content),
    )


def note_content_resource_from_read_view(
    view: NoteContentReadView[Entity, NoteContent] | None,
) -> RuntimeNoteContentResource | None:
    """Build the accepted markdown resource for a loaded note-content read view."""
    if view is None:
        return None

    read_plan = plan_runtime_note_content_read(view.entity, view.note_content)
    if read_plan.action is not RuntimeNoteContentReadAction.accepted_note:
        return None

    entity, note_content = read_plan.require_accepted_note()
    return RuntimeNoteContentResource.from_entity_and_content_state(
        entity,
        RuntimeNoteContentState.from_source(note_content),
    )


# --- Read repair for missing note_content rows ---


async def prepare_note_content_read_repair_with_default_repositories(
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
) -> NoteContentReadRepairPreflight[Project, Entity]:
    """Load DB state and decide whether storage must be read for note_content repair."""
    project = await ProjectRepository().get_by_external_id(session, project_external_id)

    entity: Entity | None = None
    note_content: NoteContent | None = None
    if project is not None:
        entity = await EntityRepository(project_id=project.id).get_by_external_id(
            session,
            entity_external_id,
        )
        if entity is not None and runtime_content_type_is_markdown(entity):
            note_content = await NoteContentRepository(project_id=project.id).get_by_entity_id(
                session,
                entity.id,
            )

    repair_plan = plan_runtime_note_content_read_repair(project, entity, note_content)
    if not repair_plan.should_read_file:
        return NoteContentReadRepairPreflight(status=repair_plan.status)

    target_project, target_entity = repair_plan.require_repair_target()
    return NoteContentReadRepairPreflight(
        status=repair_plan.status,
        target=NoteContentReadRepairTarget(project=target_project, entity=target_entity),
    )


async def run_note_content_read_repair_with_default_reconciler(
    preflight: NoteContentReadRepairPreflight[Project, Entity],
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_reader: NoteContentReadRepairFileReader[Project, Entity] | None,
    source: RuntimeNoteChangeSource,
) -> NoteContentReadRepairRun:
    """Run read repair through the default Basic Memory note_content reconciler."""
    if not preflight.should_read_file:
        return NoteContentReadRepairRun(status=preflight.status)

    if file_reader is None:
        raise RuntimeError("note-content read repair requires a file reader")

    target = preflight.require_target()
    repair_file = await file_reader.read_note_content_repair_file(target)
    if repair_file is None:
        return NoteContentReadRepairRun(status=RuntimeNoteContentReadRepairStatus.file_missing)
    if repair_file.markdown_content is None:
        return NoteContentReadRepairRun(status=RuntimeNoteContentReadRepairStatus.empty_file)

    reconciler = NoteContentReconciler(
        note_content_repository=NoteContentRepository(project_id=target.project.id),
        session_maker=session_maker,
    )
    await reconciler.reconcile(
        entity=target.entity,
        markdown_content=repair_file.markdown_content,
        observed_at=repair_file.observed_at,
        source=source,
    )
    return NoteContentReadRepairRun(status=RuntimeNoteContentReadRepairStatus.repaired)
