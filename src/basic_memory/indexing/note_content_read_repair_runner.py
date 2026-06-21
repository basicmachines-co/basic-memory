"""Repository-backed read handoffs for accepted note_content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconcileEntitySource,
    NoteContentReconciler,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import EntityRepository, NoteContentRepository, ProjectRepository
from basic_memory.runtime import (
    NoteExternalId,
    ProjectExternalId,
    ProjectId,
    ProjectPath,
    RuntimeAcceptedNoteEntitySource,
    RuntimeAcceptedNoteResponse,
    RuntimeContentType,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentReadAction,
    RuntimeNoteContentReadRepairStatus,
    RuntimeNoteContentResource,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteContentState,
    RuntimeNoteContentStateSource,
    plan_runtime_note_content_read_repair,
    plan_runtime_note_content_read,
    runtime_content_type_is_markdown,
)
from basic_memory.schemas.v2.entity import EntityResponseV2


class NoteContentReadProjectSource(Protocol):
    """Project identity needed for note-content read lookups."""

    @property
    def id(self) -> ProjectId: ...


class NoteContentReadRepairProjectSource(NoteContentReadProjectSource, Protocol):
    """Project identity needed to repair note_content from a canonical file."""

    @property
    def path(self) -> ProjectPath: ...


class NoteContentReadEntitySource(NoteContentReconcileEntitySource, Protocol):
    """Entity identity needed for note-content read lookups."""

    @property
    def content_type(self) -> RuntimeContentType: ...


class NoteContentReadResponseEntitySource(
    NoteContentReadEntitySource,
    RuntimeAcceptedNoteEntitySource,
    Protocol,
):
    """Entity shape needed for note-content response payloads."""


class NoteContentReadRepairEntitySource(NoteContentReadEntitySource, Protocol):
    """Entity identity needed to repair note_content from a canonical file."""

    @property
    def file_path(self) -> RuntimeFilePath: ...


class NoteContentReadProjectRepository[ProjectT: NoteContentReadProjectSource](Protocol):
    """Repository capability for loading the project that owns a note read."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: ProjectExternalId,
    ) -> ProjectT | None: ...


class NoteContentReadEntityRepository[EntityT: NoteContentReadEntitySource](Protocol):
    """Repository capability for loading the entity that owns a note read."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: NoteExternalId,
    ) -> EntityT | None: ...


class NoteContentReadNoteContentRepository[NoteContentT](Protocol):
    """Repository capability for loading accepted note_content by entity."""

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> NoteContentT | None: ...


class NoteContentReadRepairProjectRepository[ProjectT: NoteContentReadRepairProjectSource](
    NoteContentReadProjectRepository[ProjectT], Protocol
):
    """Project repository capability for read repair targets."""


class NoteContentReadRepairEntityRepository[EntityT: NoteContentReadRepairEntitySource](
    NoteContentReadEntityRepository[EntityT],
    Protocol,
):
    """Entity repository capability for read repair targets."""


class NoteContentReadRepairNoteContentRepository[NoteContentT](
    NoteContentReadNoteContentRepository[NoteContentT],
    Protocol,
):
    """Note-content repository capability for read repair preflight."""


class NoteContentReadRepairReconciler[EntityT: NoteContentReadRepairEntitySource](Protocol):
    """Capability that applies one observed markdown file to note_content."""

    async def reconcile(
        self,
        *,
        entity: EntityT,
        markdown_content: str,
        observed_at: datetime | None,
        source: RuntimeNoteChangeSource,
    ) -> None: ...


class NoteContentReadRepairFileReader[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
](Protocol):
    """Capability that reads the canonical markdown file for read repair."""

    async def read_note_content_repair_file(
        self,
        target: NoteContentReadRepairTarget[ProjectT, EntityT],
    ) -> NoteContentReadRepairFile | None: ...


class NoteContentReadRepositories[
    ProjectT: NoteContentReadProjectSource,
    EntityT: NoteContentReadEntitySource,
    NoteContentT,
](Protocol):
    """Repository capability set for hot note-content reads."""

    def project_repository(self) -> NoteContentReadProjectRepository[ProjectT]: ...

    def entity_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadEntityRepository[EntityT]: ...

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadNoteContentRepository[NoteContentT]: ...


class NoteContentReadRepairRepositories[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
    NoteContentT,
](Protocol):
    """Repository capability set for note-content read repair preflight."""

    def project_repository(self) -> NoteContentReadRepairProjectRepository[ProjectT]: ...

    def entity_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadRepairEntityRepository[EntityT]: ...

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadRepairNoteContentRepository[NoteContentT]: ...


class NoteContentReadRepairReconcilerProvider[EntityT: NoteContentReadRepairEntitySource](Protocol):
    """Capability that supplies the reconciler for one read-repair project."""

    def reconciler(
        self,
        project_id: ProjectId,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> NoteContentReadRepairReconciler[EntityT]: ...


@dataclass(frozen=True, slots=True)
class NoteContentReadView[
    EntityT: NoteContentReadEntitySource,
    NoteContentT,
]:
    """Joined entity plus accepted note_content used by hot note reads."""

    entity: EntityT
    note_content: NoteContentT | None


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairTarget[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
]:
    """Storage object identity needed after DB preflight allows read repair."""

    project: ProjectT
    entity: EntityT


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairFile:
    """Canonical markdown content observed by a read-repair storage adapter."""

    markdown_content: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairPreflight[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
]:
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


def note_content_read_project_repository() -> NoteContentReadProjectRepository[Project]:
    """Create the default project repository for note-content reads."""
    return ProjectRepository()


def note_content_read_entity_repository(
    project_id: ProjectId,
) -> NoteContentReadEntityRepository[Entity]:
    """Create the default entity repository for note-content reads."""
    return EntityRepository(project_id=project_id)


def note_content_read_note_content_repository(
    project_id: ProjectId,
) -> NoteContentReadNoteContentRepository[NoteContent]:
    """Create the default note_content repository for note-content reads."""
    return NoteContentRepository(project_id=project_id)


@dataclass(frozen=True, slots=True)
class DefaultNoteContentReadRepositories:
    """Default repository capability set for hot note-content reads."""

    def project_repository(self) -> NoteContentReadProjectRepository[Project]:
        return note_content_read_project_repository()

    def entity_repository(self, project_id: ProjectId) -> NoteContentReadEntityRepository[Entity]:
        return note_content_read_entity_repository(project_id)

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadNoteContentRepository[NoteContent]:
        return note_content_read_note_content_repository(project_id)


async def load_note_content_read_view[
    ProjectT: NoteContentReadProjectSource,
    EntityT: NoteContentReadEntitySource,
    NoteContentT,
](
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
    repositories: NoteContentReadRepositories[ProjectT, EntityT, NoteContentT],
) -> NoteContentReadView[EntityT, NoteContentT] | None:
    """Load the DB view needed by hot note-content reads."""
    project_repository = repositories.project_repository()
    project = await project_repository.get_by_external_id(session, project_external_id)
    if project is None:
        return None

    entity_repository = repositories.entity_repository(project.id)
    entity = await entity_repository.get_by_external_id(session, entity_external_id)
    if entity is None:
        return None

    note_content = None
    if runtime_content_type_is_markdown(entity):
        note_content_repository = repositories.note_content_repository(project.id)
        note_content = await note_content_repository.get_by_entity_id(session, entity.id)

    return NoteContentReadView(entity=entity, note_content=note_content)


async def load_note_content_read_view_with_default_repositories(
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
) -> NoteContentReadView[Entity, NoteContent] | None:
    """Load the hot read view through the default Basic Memory repositories."""
    return await load_note_content_read_view(
        session,
        project_external_id=project_external_id,
        entity_external_id=entity_external_id,
        repositories=DefaultNoteContentReadRepositories(),
    )


def note_content_response_payload_from_read_view[
    EntityT: NoteContentReadResponseEntitySource,
    NoteContentT: RuntimeNoteContentStateSource,
](
    view: NoteContentReadView[EntityT, NoteContentT] | None,
) -> RuntimeNoteContentResponsePayload | None:
    """Build the typed response payload for a loaded note-content read view."""
    if view is None:
        return None

    read_plan = plan_runtime_note_content_read(view.entity, view.note_content)
    if read_plan.action is RuntimeNoteContentReadAction.entity_metadata:
        return EntityResponseV2.model_validate(read_plan.require_entity_metadata()).model_dump(
            mode="json"
        )

    if read_plan.action is not RuntimeNoteContentReadAction.accepted_note:
        return None

    entity, note_content = read_plan.require_accepted_note()
    return RuntimeAcceptedNoteResponse.from_entity_and_content_state(
        entity=entity,
        note_content=RuntimeNoteContentState.from_source(note_content),
    )


def note_content_resource_from_read_view[
    EntityT: NoteContentReadEntitySource,
    NoteContentT: RuntimeNoteContentStateSource,
](
    view: NoteContentReadView[EntityT, NoteContentT] | None,
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


def note_content_read_repair_project_repository() -> NoteContentReadRepairProjectRepository[
    Project
]:
    """Create the default project repository for note-content read repair."""
    return ProjectRepository()


def note_content_read_repair_entity_repository(
    project_id: ProjectId,
) -> NoteContentReadRepairEntityRepository[Entity]:
    """Create the default entity repository for note-content read repair."""
    return EntityRepository(project_id=project_id)


def note_content_read_repair_note_content_repository(
    project_id: ProjectId,
) -> NoteContentReadRepairNoteContentRepository[NoteContent]:
    """Create the default note_content repository for note-content read repair."""
    return NoteContentRepository(project_id=project_id)


def note_content_read_repair_reconciler(
    project_id: ProjectId,
    session_maker: async_sessionmaker[AsyncSession],
) -> NoteContentReadRepairReconciler[Entity]:
    """Create the default note_content reconciler for read repair."""
    return NoteContentReconciler(
        note_content_repository=NoteContentRepository(project_id=project_id),
        session_maker=session_maker,
    )


@dataclass(frozen=True, slots=True)
class DefaultNoteContentReadRepairRepositories:
    """Default repository capability set for note-content read repair preflight."""

    def project_repository(self) -> NoteContentReadRepairProjectRepository[Project]:
        return note_content_read_repair_project_repository()

    def entity_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadRepairEntityRepository[Entity]:
        return note_content_read_repair_entity_repository(project_id)

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> NoteContentReadRepairNoteContentRepository[NoteContent]:
        return note_content_read_repair_note_content_repository(project_id)


@dataclass(frozen=True, slots=True)
class DefaultNoteContentReadRepairReconcilerProvider:
    """Default reconciler provider for note-content read repair."""

    def reconciler(
        self,
        project_id: ProjectId,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> NoteContentReadRepairReconciler[Entity]:
        return note_content_read_repair_reconciler(project_id, session_maker)


async def prepare_note_content_read_repair[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
    NoteContentT,
](
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
    repositories: NoteContentReadRepairRepositories[ProjectT, EntityT, NoteContentT],
) -> NoteContentReadRepairPreflight[ProjectT, EntityT]:
    """Load DB state and decide whether storage must be read for note_content repair."""
    project_repository = repositories.project_repository()
    project = await project_repository.get_by_external_id(session, project_external_id)

    entity: EntityT | None = None
    note_content: NoteContentT | None = None
    if project is not None:
        entity_repository = repositories.entity_repository(project.id)
        entity = await entity_repository.get_by_external_id(session, entity_external_id)
        if entity is not None and runtime_content_type_is_markdown(entity):
            note_content_repository = repositories.note_content_repository(project.id)
            note_content = await note_content_repository.get_by_entity_id(session, entity.id)

    repair_plan = plan_runtime_note_content_read_repair(project, entity, note_content)
    if not repair_plan.should_read_file:
        return NoteContentReadRepairPreflight(status=repair_plan.status)

    target_project, target_entity = repair_plan.require_repair_target()
    return NoteContentReadRepairPreflight(
        status=repair_plan.status,
        target=NoteContentReadRepairTarget(project=target_project, entity=target_entity),
    )


async def prepare_note_content_read_repair_with_default_repositories(
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
) -> NoteContentReadRepairPreflight[Project, Entity]:
    """Prepare read repair through the default Basic Memory repositories."""
    return await prepare_note_content_read_repair(
        session,
        project_external_id=project_external_id,
        entity_external_id=entity_external_id,
        repositories=DefaultNoteContentReadRepairRepositories(),
    )


async def apply_note_content_read_repair[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
](
    target: NoteContentReadRepairTarget[ProjectT, EntityT],
    *,
    session_maker: async_sessionmaker[AsyncSession],
    markdown_content: str,
    observed_at: datetime | None,
    source: RuntimeNoteChangeSource,
    reconciler_provider: NoteContentReadRepairReconcilerProvider[EntityT],
) -> None:
    """Apply observed storage markdown to note_content through the selected reconciler."""
    reconciler = reconciler_provider.reconciler(target.project.id, session_maker)
    await reconciler.reconcile(
        entity=target.entity,
        markdown_content=markdown_content,
        observed_at=observed_at,
        source=source,
    )


async def apply_note_content_read_repair_with_default_reconciler(
    target: NoteContentReadRepairTarget[Project, Entity],
    *,
    session_maker: async_sessionmaker[AsyncSession],
    markdown_content: str,
    observed_at: datetime | None,
    source: RuntimeNoteChangeSource,
) -> None:
    """Apply read repair through the default Basic Memory note_content reconciler."""
    await apply_note_content_read_repair(
        target,
        session_maker=session_maker,
        markdown_content=markdown_content,
        observed_at=observed_at,
        source=source,
        reconciler_provider=DefaultNoteContentReadRepairReconcilerProvider(),
    )


async def run_note_content_read_repair[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
](
    preflight: NoteContentReadRepairPreflight[ProjectT, EntityT],
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_reader: NoteContentReadRepairFileReader[ProjectT, EntityT] | None,
    source: RuntimeNoteChangeSource,
    reconciler_provider: NoteContentReadRepairReconcilerProvider[EntityT],
) -> NoteContentReadRepairRun:
    """Run storage-neutral read repair after the database preflight decision."""
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

    await apply_note_content_read_repair(
        target,
        session_maker=session_maker,
        markdown_content=repair_file.markdown_content,
        observed_at=repair_file.observed_at,
        source=source,
        reconciler_provider=reconciler_provider,
    )
    return NoteContentReadRepairRun(status=RuntimeNoteContentReadRepairStatus.repaired)


async def run_note_content_read_repair_with_default_reconciler(
    preflight: NoteContentReadRepairPreflight[Project, Entity],
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_reader: NoteContentReadRepairFileReader[Project, Entity] | None,
    source: RuntimeNoteChangeSource,
) -> NoteContentReadRepairRun:
    """Run read repair through the default Basic Memory note_content reconciler."""
    return await run_note_content_read_repair(
        preflight,
        session_maker=session_maker,
        file_reader=file_reader,
        source=source,
        reconciler_provider=DefaultNoteContentReadRepairReconcilerProvider(),
    )
