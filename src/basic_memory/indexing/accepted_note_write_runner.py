"""Portable persistence handoffs for accepted note writes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import (
    AcceptedNoteSearchRow,
    build_accepted_note_search_row,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.repository import AcceptedNoteContentWrite, NoteContentRepository
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.entity_repository import (
    AcceptedPendingEntityWrite,
    EntityMetadata,
    EntityRepository,
)
from basic_memory.runtime import (
    ProjectId,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
)


class AcceptedPreparedEntityFields(Protocol):
    """Prepared Basic Memory entity fields accepted before file materialization."""

    @property
    def title(self) -> str: ...

    @property
    def note_type(self) -> str: ...

    @property
    def entity_metadata(self) -> EntityMetadata: ...

    @property
    def content_type(self) -> str: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def file_path(self) -> RuntimeFilePath: ...


class AcceptedPreparedEntityWriteSource(Protocol):
    """Prepared markdown/entity state produced by Basic Memory note semantics."""

    @property
    def entity_fields(self) -> AcceptedPreparedEntityFields: ...


class AcceptedNoteContentEntitySource(Protocol):
    """Entity identity required to accept one note_content snapshot."""

    @property
    def id(self) -> RuntimeEntityId: ...

    @property
    def project_id(self) -> ProjectId: ...


class AcceptedNoteSearchEntitySource(AcceptedNoteContentEntitySource, Protocol):
    """Entity fields required to refresh the hot accepted-note search row."""

    @property
    def title(self) -> str | None: ...

    @property
    def note_type(self) -> str | None: ...

    @property
    def entity_metadata(self) -> Mapping[str, object] | None: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def file_path(self) -> RuntimeFilePath: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def updated_at(self) -> datetime: ...


class AcceptedPendingEntityRepository(Protocol):
    """Repository capability for inserting one pending accepted entity."""

    async def create_pending_accepted_entity(
        self,
        session: AsyncSession,
        write: AcceptedPendingEntityWrite,
    ) -> Entity: ...


class AcceptedNoteContentRepository(Protocol):
    """Repository capability for accepting one note_content snapshot."""

    async def accept_write(
        self,
        session: AsyncSession,
        write: AcceptedNoteContentWrite,
    ) -> NoteContent: ...


class AcceptedNoteSearchRowRepository(Protocol):
    """Repository capability for replacing one accepted-note search row."""

    async def refresh_entity(
        self,
        session: AsyncSession,
        row: AcceptedNoteSearchRow,
    ) -> None: ...


type AcceptedPendingEntityRepositoryFactory = Callable[[ProjectId], AcceptedPendingEntityRepository]
type AcceptedNoteContentRepositoryFactory = Callable[[ProjectId], AcceptedNoteContentRepository]
type AcceptedNoteSearchRepositoryFactory = Callable[[ProjectId], AcceptedNoteSearchRowRepository]


def accepted_entity_repository_for_project(
    project_id: ProjectId,
) -> AcceptedPendingEntityRepository:
    """Create the core repository adapter for pending accepted entities."""
    return EntityRepository(project_id=project_id)


def accepted_note_content_repository_for_project(
    project_id: ProjectId,
) -> AcceptedNoteContentRepository:
    """Create the core repository adapter for accepted note_content rows."""
    return NoteContentRepository(project_id=project_id)


def accepted_note_search_repository_for_project(
    project_id: ProjectId,
) -> AcceptedNoteSearchRowRepository:
    """Create the core repository adapter for accepted-note search rows."""
    return AcceptedNoteSearchRepository(project_id=project_id)


def accepted_pending_entity_write_from_prepared(
    prepared: AcceptedPreparedEntityWriteSource,
    *,
    now: datetime,
    user_profile_value: str | None,
    external_id: str | None = None,
) -> AcceptedPendingEntityWrite:
    """Map prepared Basic Memory entity fields to the pending entity DB write."""
    fields = prepared.entity_fields
    return AcceptedPendingEntityWrite(
        title=fields.title,
        note_type=fields.note_type,
        entity_metadata=fields.entity_metadata,
        content_type=fields.content_type,
        permalink=fields.permalink,
        file_path=fields.file_path,
        created_at=now,
        updated_at=now,
        created_by=user_profile_value,
        last_updated_by=user_profile_value,
        external_id=external_id,
    )


async def create_accepted_pending_entity(
    session: AsyncSession,
    *,
    prepared: AcceptedPreparedEntityWriteSource,
    project_id: ProjectId,
    now: datetime,
    user_profile_value: str | None,
    external_id: str | None = None,
    repository_factory: AcceptedPendingEntityRepositoryFactory = (
        accepted_entity_repository_for_project
    ),
) -> Entity:
    """Insert a prepared accepted entity row without materializing a file."""
    repository = repository_factory(project_id)
    return await repository.create_pending_accepted_entity(
        session,
        accepted_pending_entity_write_from_prepared(
            prepared,
            now=now,
            user_profile_value=user_profile_value,
            external_id=external_id,
        ),
    )


def accepted_note_content_write_from_markdown(
    *,
    entity_id: RuntimeEntityId,
    markdown_content: str,
    db_version: RuntimeNoteContentVersion,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
) -> AcceptedNoteContentWrite:
    """Build the repository write for one accepted note_content snapshot."""
    return AcceptedNoteContentWrite(
        entity_id=entity_id,
        markdown_content=markdown_content,
        db_version=db_version,
        db_checksum=db_checksum,
        last_source=last_source,
        updated_at=updated_at,
    )


async def accept_note_content_write(
    session: AsyncSession,
    *,
    entity: AcceptedNoteContentEntitySource,
    markdown_content: str,
    db_version: RuntimeNoteContentVersion,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    repository_factory: AcceptedNoteContentRepositoryFactory = (
        accepted_note_content_repository_for_project
    ),
) -> NoteContent:
    """Accept markdown into note_content before object storage catches up."""
    repository = repository_factory(entity.project_id)
    return await repository.accept_write(
        session,
        accepted_note_content_write_from_markdown(
            entity_id=entity.id,
            markdown_content=markdown_content,
            db_version=db_version,
            db_checksum=db_checksum,
            last_source=last_source,
            updated_at=updated_at,
        ),
    )


def accepted_note_search_row_from_entity(
    entity: AcceptedNoteSearchEntitySource,
    *,
    search_content: str,
) -> AcceptedNoteSearchRow:
    """Build the hot search row for one accepted note snapshot."""
    return build_accepted_note_search_row(
        entity_id=entity.id,
        title=entity.title,
        note_type=entity.note_type,
        entity_metadata=entity.entity_metadata,
        permalink=entity.permalink,
        file_path=entity.file_path,
        search_content=search_content,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        project_id=entity.project_id,
    )


async def refresh_accepted_note_search_index(
    session: AsyncSession,
    *,
    entity: AcceptedNoteSearchEntitySource,
    search_content: str,
    repository_factory: AcceptedNoteSearchRepositoryFactory = (
        accepted_note_search_repository_for_project
    ),
) -> None:
    """Refresh the hot accepted-note search row inside the caller's transaction."""
    repository = repository_factory(entity.project_id)
    await repository.refresh_entity(
        session,
        accepted_note_search_row_from_entity(entity, search_content=search_content),
    )
