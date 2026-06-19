"""Repository-backed read-repair handoffs for accepted note_content."""

from __future__ import annotations

from collections.abc import Callable
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
    RuntimeContentType,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentReadRepairStatus,
    plan_runtime_note_content_read_repair,
    runtime_content_type_is_markdown,
)


class NoteContentReadRepairProjectSource(Protocol):
    """Project identity needed to repair note_content from a canonical file."""

    @property
    def id(self) -> ProjectId: ...

    @property
    def path(self) -> ProjectPath: ...


class NoteContentReadRepairEntitySource(NoteContentReconcileEntitySource, Protocol):
    """Entity identity needed to repair note_content from a canonical file."""

    @property
    def content_type(self) -> RuntimeContentType: ...

    @property
    def file_path(self) -> RuntimeFilePath: ...


class NoteContentReadRepairProjectRepository[ProjectT: NoteContentReadRepairProjectSource](
    Protocol
):
    """Repository capability for loading the project that owns a note read."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: ProjectExternalId,
    ) -> ProjectT | None: ...


class NoteContentReadRepairEntityRepository[EntityT: NoteContentReadRepairEntitySource](Protocol):
    """Repository capability for loading the entity that owns a note read."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: NoteExternalId,
    ) -> EntityT | None: ...


class NoteContentReadRepairNoteContentRepository[NoteContentT](Protocol):
    """Repository capability for checking whether note_content already exists."""

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> NoteContentT | None: ...


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


type NoteContentReadRepairProjectRepositoryFactory[ProjectT: NoteContentReadRepairProjectSource] = (
    Callable[[], NoteContentReadRepairProjectRepository[ProjectT]]
)
type NoteContentReadRepairEntityRepositoryFactory[EntityT: NoteContentReadRepairEntitySource] = (
    Callable[[ProjectId], NoteContentReadRepairEntityRepository[EntityT]]
)
type NoteContentReadRepairNoteContentRepositoryFactory[NoteContentT] = Callable[
    [ProjectId], NoteContentReadRepairNoteContentRepository[NoteContentT]
]
type NoteContentReadRepairReconcilerFactory[EntityT: NoteContentReadRepairEntitySource] = Callable[
    [ProjectId, async_sessionmaker[AsyncSession]], NoteContentReadRepairReconciler[EntityT]
]


@dataclass(frozen=True, slots=True)
class NoteContentReadRepairTarget[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
]:
    """Storage object identity needed after DB preflight allows read repair."""

    project: ProjectT
    entity: EntityT


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


async def prepare_note_content_read_repair[
    ProjectT: NoteContentReadRepairProjectSource,
    EntityT: NoteContentReadRepairEntitySource,
    NoteContentT,
](
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
    project_repository_factory: NoteContentReadRepairProjectRepositoryFactory[ProjectT],
    entity_repository_factory: NoteContentReadRepairEntityRepositoryFactory[EntityT],
    note_content_repository_factory: NoteContentReadRepairNoteContentRepositoryFactory[
        NoteContentT
    ],
) -> NoteContentReadRepairPreflight[ProjectT, EntityT]:
    """Load DB state and decide whether storage must be read for note_content repair."""
    project_repository = project_repository_factory()
    project = await project_repository.get_by_external_id(session, project_external_id)

    entity: EntityT | None = None
    note_content: NoteContentT | None = None
    if project is not None:
        entity_repository = entity_repository_factory(project.id)
        entity = await entity_repository.get_by_external_id(session, entity_external_id)
        if entity is not None and runtime_content_type_is_markdown(entity):
            note_content_repository = note_content_repository_factory(project.id)
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
        project_repository_factory=note_content_read_repair_project_repository,
        entity_repository_factory=note_content_read_repair_entity_repository,
        note_content_repository_factory=note_content_read_repair_note_content_repository,
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
    reconciler_factory: NoteContentReadRepairReconcilerFactory[EntityT],
) -> None:
    """Apply observed storage markdown to note_content through the selected reconciler."""
    reconciler = reconciler_factory(target.project.id, session_maker)
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
        reconciler_factory=note_content_read_repair_reconciler,
    )
