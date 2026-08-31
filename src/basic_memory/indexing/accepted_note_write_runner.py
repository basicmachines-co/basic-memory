"""Portable persistence handoffs for accepted note writes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import file_utils
from basic_memory.indexing.accepted_note_search import build_accepted_note_search_row
from basic_memory.indexing.external_file_delete_runner import (
    relation_cleanup_sources_for_deleted_entity,
)
from basic_memory.indexing.models import IndexedObservation, IndexedRelation, IndexedSection
from basic_memory.indexing.relation_persistence import (
    ObservationGenerationStore,
    RelationGenerationPublication,
    RelationGenerationStore,
    SectionGenerationStore,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.repository import (
    AcceptedNoteContentWrite,
    AcceptedObservationWrite,
    AcceptedRelationWrite,
    AcceptedSectionWrite,
)
from basic_memory.repository.accepted_note_search_row import AcceptedNoteSearchRow
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteContentWriteSource,
    RuntimeDeletedNoteFileChecksumSource,
    RuntimeDeletedNoteFileDeleteEntitySource,
    RuntimePendingNoteFileDelete,
    plan_accepted_note_content_write,
    plan_accepted_note_delete_change,
)
from basic_memory.runtime.storage import (
    ProjectId,
    RuntimeEntityId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.note_preparation import (
    PreparedEntityFields,
    PreparedEntityMove,
    PreparedEntityWrite,
    apply_prepared_entity_fields,
)


class AcceptedNoteCreatePreparer(Protocol):
    """Capability that derives accepted markdown for a new note."""

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = ...,
        skip_conflict_check: bool = ...,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityWrite: ...


class AcceptedNoteReplacePreparer(Protocol):
    """Capability that derives accepted markdown for a full note replacement."""

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityWrite: ...


class AcceptedNoteEditPreparer(Protocol):
    """Capability that derives accepted markdown for a partial note edit."""

    async def prepare_edit_entity_content(
        self,
        entity: Entity,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: str | None = ...,
        find_text: str | None = ...,
        expected_replacements: int = ...,
        replace_subsections: bool = ...,
        metadata: dict[str, Any] | None = ...,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityWrite: ...


class AcceptedNoteMovePreparer(Protocol):
    """Capability that derives accepted markdown for a note move."""

    async def prepare_move_entity_content(
        self,
        entity: Entity,
        current_content: str,
        destination_path: str,
        *,
        should_update_permalink: bool,
        session: AsyncSession | None = ...,
    ) -> PreparedEntityMove: ...

    async def verify_move_destination_absent(
        self,
        *,
        source_file_path: str,
        destination_file_path: str,
    ) -> None: ...


class AcceptedNoteSelfRelationResolver(Protocol):
    """Resolve only ambiguity-safe self-links while accepted bytes are in hand."""

    async def resolve_deferred_self_relation(
        self,
        target: str,
        entity: Entity,
        session: AsyncSession | None = ...,
    ) -> Entity | None: ...


class AcceptedNoteDeleteEntitySource(RuntimeDeletedNoteFileDeleteEntitySource, Protocol):
    """Entity identity required to delete one accepted note row."""


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

    async def delete_entity(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> None: ...

    async def delete_entity_vectors(
        self,
        session: AsyncSession,
        entity_id: RuntimeEntityId,
    ) -> None: ...


class AcceptedNoteObservationRepository(ObservationGenerationStore, Protocol):
    """Generation-fenced observation persistence for accepted note writes."""


class AcceptedNoteSectionRepository(SectionGenerationStore, Protocol):
    """Generation-fenced section persistence for accepted note writes."""


class AcceptedNoteRelationRepository(RelationGenerationStore, Protocol):
    """Generation-fenced relation persistence for accepted note writes."""


class AcceptedNoteWriteRepositories(Protocol):
    """Repository capability set needed by accepted-note DB-first writes."""

    def pending_entity_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedPendingEntityRepository: ...

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteContentRepository: ...

    def search_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteSearchRowRepository: ...

    def observation_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteObservationRepository: ...

    def section_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteSectionRepository: ...

    def relation_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteRelationRepository: ...


@dataclass(frozen=True, slots=True)
class AcceptedPreparedNoteWrite:
    """Prepared accepted markdown plus the checksum of that exact markdown."""

    prepared: PreparedEntityWrite
    db_checksum: RuntimeNoteContentChecksum


@dataclass(frozen=True, slots=True)
class AcceptedPreparedNoteMove:
    """Accepted markdown, path, permalink, and checksum for one DB-first move."""

    file_path: RuntimeFilePath
    markdown_content: str
    search_content: str
    permalink: str | None
    db_checksum: RuntimeNoteContentChecksum
    observations: tuple[AcceptedObservationWrite, ...]
    sections: tuple[AcceptedSectionWrite, ...]
    relations: tuple[AcceptedRelationWrite, ...]


@dataclass(frozen=True, slots=True)
class AcceptedPersistedNoteWrite:
    """Accepted note_content row plus any cleanup for a superseded materialized file."""

    note_content: NoteContent
    previous_file_delete: RuntimePendingNoteFileDelete | None = None
    relation_publication: RelationGenerationPublication | None = None


async def prepare_accepted_note_create(
    preparer: AcceptedNoteCreatePreparer,
    data: EntitySchema,
    *,
    check_storage_exists: bool,
    skip_conflict_check: bool = False,
    session: AsyncSession | None = None,
) -> AcceptedPreparedNoteWrite:
    """Prepare one DB-first note create and checksum the accepted markdown."""
    prepared = await preparer.prepare_create_entity_content(
        data,
        check_storage_exists=check_storage_exists,
        skip_conflict_check=skip_conflict_check,
        session=session,
    )
    return AcceptedPreparedNoteWrite(
        prepared=prepared,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
    )


async def prepare_accepted_note_replace(
    preparer: AcceptedNoteReplacePreparer,
    session: AsyncSession,
    *,
    entity: Entity,
    data: EntitySchema,
    current_note_content: NoteContent,
    user_profile_value: str | None,
) -> AcceptedPreparedNoteWrite:
    """Prepare a full accepted replacement and apply its entity fields."""
    prepared = await preparer.prepare_update_entity_content(
        entity,
        data,
        str(current_note_content.markdown_content),
        session=session,
    )
    result = AcceptedPreparedNoteWrite(
        prepared=prepared,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
    )
    apply_accepted_prepared_entity_fields(
        entity,
        prepared.entity_fields,
        user_profile_value=user_profile_value,
    )
    await session.flush()
    return result


async def prepare_accepted_note_edit(
    preparer: AcceptedNoteEditPreparer,
    session: AsyncSession,
    *,
    entity: Entity,
    current_note_content: NoteContent,
    operation: str,
    content: str,
    section: str | None,
    find_text: str | None,
    expected_replacements: int,
    replace_subsections: bool,
    user_profile_value: str | None,
    metadata: dict[str, Any] | None = None,
) -> AcceptedPreparedNoteWrite:
    """Prepare a partial accepted edit and apply its entity fields."""
    prepared = await preparer.prepare_edit_entity_content(
        entity,
        str(current_note_content.markdown_content),
        operation=operation,
        content=content,
        section=section,
        find_text=find_text,
        expected_replacements=expected_replacements,
        replace_subsections=replace_subsections,
        metadata=metadata,
        session=session,
    )
    result = AcceptedPreparedNoteWrite(
        prepared=prepared,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
    )
    apply_accepted_prepared_entity_fields(
        entity,
        prepared.entity_fields,
        user_profile_value=user_profile_value,
    )
    await session.flush()
    return result


async def prepare_accepted_note_move(
    preparer: AcceptedNoteMovePreparer,
    session: AsyncSession,
    *,
    entity: Entity,
    current_note_content: NoteContent,
    accepted_file_path: RuntimeFilePath,
    should_update_permalink: bool,
    user_profile_value: str | None,
) -> AcceptedPreparedNoteMove:
    """Prepare a DB-first move and apply the accepted path/permalink fields."""
    current_content = str(current_note_content.markdown_content)
    prepared = await preparer.prepare_move_entity_content(
        entity,
        current_content,
        accepted_file_path,
        should_update_permalink=should_update_permalink,
        session=session,
    )

    result = AcceptedPreparedNoteMove(
        file_path=prepared.file_path.as_posix(),
        markdown_content=prepared.markdown_content,
        search_content=prepared.search_content,
        permalink=prepared.permalink,
        db_checksum=await file_utils.compute_checksum(prepared.markdown_content),
        observations=prepared.observations,
        sections=prepared.sections,
        relations=prepared.relations,
    )
    entity.file_path = result.file_path
    entity.permalink = result.permalink
    entity.last_updated_by = user_profile_value
    await session.flush()
    return result


def apply_accepted_prepared_entity_fields(
    entity: Entity,
    entity_fields: PreparedEntityFields,
    *,
    user_profile_value: str | None,
) -> None:
    """Copy prepared accepted markdown fields onto an entity row."""
    apply_prepared_entity_fields(
        entity,
        entity_fields,
        user_profile_value=user_profile_value,
    )


def accepted_pending_entity_write_from_prepared(
    prepared: PreparedEntityWrite,
    *,
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
        created_at=fields.created_at,
        updated_at=fields.updated_at,
        created_by=user_profile_value,
        last_updated_by=user_profile_value,
        external_id=external_id,
    )


async def create_accepted_pending_entity(
    session: AsyncSession,
    *,
    prepared: PreparedEntityWrite,
    project_id: ProjectId,
    user_profile_value: str | None,
    external_id: str | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> Entity:
    """Insert a prepared accepted entity row without materializing a file."""
    repository = repositories.pending_entity_repository(project_id)
    return await repository.create_pending_accepted_entity(
        session,
        accepted_pending_entity_write_from_prepared(
            prepared,
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
    entity: Entity,
    markdown_content: str,
    db_version: RuntimeNoteContentVersion,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    repositories: AcceptedNoteWriteRepositories,
) -> NoteContent:
    """Accept markdown into note_content before object storage catches up."""
    repository = repositories.note_content_repository(entity.project_id)
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
    entity: Entity,
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
    entity: Entity,
    search_content: str,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Refresh the hot accepted-note search row inside the caller's transaction."""
    repository = repositories.search_repository(entity.project_id)
    await repository.refresh_entity(
        session,
        accepted_note_search_row_from_entity(entity, search_content=search_content),
    )


async def delete_accepted_note_search_index(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Remove all search rows for an accepted-note entity inside the caller's transaction."""
    repository = repositories.search_repository(project_id)
    await repository.delete_entity(session, entity_id)


async def delete_accepted_note_vectors(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
    repositories: AcceptedNoteWriteRepositories,
) -> None:
    """Remove semantic vectors for an accepted-note entity inside the caller's transaction."""
    repository = repositories.search_repository(project_id)
    await repository.delete_entity_vectors(session, entity_id)


async def _persist_accepted_note_content_and_search(
    session: AsyncSession,
    *,
    entity: Entity,
    markdown_content: str,
    search_content: str,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    current_note_content: RuntimeAcceptedNoteContentWriteSource | None = None,
    existing_file_path: RuntimeFilePath | None = None,
    accepted_file_path: RuntimeFilePath | None = None,
    source_file_checksum: RuntimeFileChecksum | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> AcceptedPersistedNoteWrite:
    """Internal content/search phase shared by complete snapshots and moves."""
    content_write = plan_accepted_note_content_write(
        project_id=entity.project_id,
        entity_id=entity.id,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path or entity.file_path,
        current_note_content=current_note_content,
        source_file_checksum=source_file_checksum,
    )
    note_content = await accept_note_content_write(
        session,
        entity=entity,
        markdown_content=markdown_content,
        db_version=content_write.db_version,
        db_checksum=db_checksum,
        last_source=last_source,
        updated_at=updated_at,
        repositories=repositories,
    )
    await refresh_accepted_note_search_index(
        session,
        entity=entity,
        search_content=search_content,
        repositories=repositories,
    )
    return AcceptedPersistedNoteWrite(
        note_content=note_content,
        previous_file_delete=content_write.previous_file_delete,
    )


async def accepted_relation_generation_publication(
    session: AsyncSession,
    *,
    entity: Entity,
    note_content: NoteContent,
    observations: Sequence[AcceptedObservationWrite],
    sections: Sequence[AcceptedSectionWrite],
    relations: Sequence[AcceptedRelationWrite],
    self_relation_resolver: AcceptedNoteSelfRelationResolver,
) -> RelationGenerationPublication:
    """Carry the parsed graph plus ambiguity-safe self targets into publication."""
    indexed_observations = tuple(
        IndexedObservation(
            content=observation.content,
            category=observation.category,
            context=observation.context,
            tags=observation.tags,
        )
        for observation in observations
    )
    indexed_sections = tuple(
        IndexedSection(
            heading=section.heading,
            level=section.level,
            heading_path=section.heading_path,
            duplicate_index=section.duplicate_index,
            start_line=section.start_line,
            end_line=section.end_line,
            start_offset=section.start_offset,
            end_offset=section.end_offset,
        )
        for section in sections
    )
    indexed_relations: list[IndexedRelation] = []
    for relation in relations:
        target_id = relation.target_id
        if target_id is None:
            target = await self_relation_resolver.resolve_deferred_self_relation(
                relation.target_name,
                entity,
                session=session,
            )
            target_id = target.id if target is not None else None
        if target_id is not None and target_id != entity.id:
            raise ValueError("Accepted relation pre-resolution is restricted to self-links")
        indexed_relations.append(
            IndexedRelation(
                relation_type=relation.relation_type,
                target_name=relation.target_name,
                context=relation.context,
                target_id=target_id,
            )
        )

    return RelationGenerationPublication(
        project_id=entity.project_id,
        entity_id=entity.id,
        generation=note_content.db_version,
        relations=tuple(indexed_relations),
        observations=indexed_observations,
        sections=indexed_sections,
    )


async def persist_accepted_note_snapshot(
    session: AsyncSession,
    *,
    entity: Entity,
    prepared: PreparedEntityWrite,
    db_checksum: RuntimeNoteContentChecksum,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    current_note_content: RuntimeAcceptedNoteContentWriteSource | None = None,
    existing_file_path: RuntimeFilePath | None = None,
    accepted_file_path: RuntimeFilePath | None = None,
    source_file_checksum: RuntimeFileChecksum | None = None,
    self_relation_resolver: AcceptedNoteSelfRelationResolver,
    repositories: AcceptedNoteWriteRepositories,
) -> AcceptedPersistedNoteWrite:
    """Persist one complete accepted Markdown snapshot in the caller's transaction."""
    persisted = await _persist_accepted_note_content_and_search(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        search_content=prepared.search_content,
        db_checksum=db_checksum,
        last_source=last_source,
        updated_at=updated_at,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=accepted_file_path,
        source_file_checksum=source_file_checksum,
        repositories=repositories,
    )
    return AcceptedPersistedNoteWrite(
        note_content=persisted.note_content,
        previous_file_delete=persisted.previous_file_delete,
        relation_publication=await accepted_relation_generation_publication(
            session,
            entity=entity,
            note_content=persisted.note_content,
            observations=prepared.observations,
            sections=prepared.sections,
            relations=prepared.relations,
            self_relation_resolver=self_relation_resolver,
        ),
    )


async def persist_accepted_note_move(
    session: AsyncSession,
    *,
    entity: Entity,
    prepared: AcceptedPreparedNoteMove,
    last_source: RuntimeNoteChangeSource | None,
    updated_at: datetime,
    current_note_content: RuntimeAcceptedNoteContentWriteSource,
    existing_file_path: RuntimeFilePath,
    source_file_checksum: RuntimeFileChecksum | None = None,
    self_relation_resolver: AcceptedNoteSelfRelationResolver,
    repositories: AcceptedNoteWriteRepositories,
) -> AcceptedPersistedNoteWrite:
    """Persist the explicitly narrower content/search state for an accepted move."""
    persisted = await _persist_accepted_note_content_and_search(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        search_content=prepared.search_content,
        db_checksum=prepared.db_checksum,
        last_source=last_source,
        updated_at=updated_at,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=prepared.file_path,
        source_file_checksum=source_file_checksum,
        repositories=repositories,
    )
    return AcceptedPersistedNoteWrite(
        note_content=persisted.note_content,
        previous_file_delete=persisted.previous_file_delete,
        relation_publication=await accepted_relation_generation_publication(
            session,
            entity=entity,
            note_content=persisted.note_content,
            observations=prepared.observations,
            sections=prepared.sections,
            relations=prepared.relations,
            self_relation_resolver=self_relation_resolver,
        ),
    )


async def delete_accepted_note_entity(
    session: AsyncSession,
    *,
    entity: AcceptedNoteDeleteEntitySource,
) -> None:
    """Delete the accepted entity row inside the caller-owned transaction."""
    await session.delete(entity)


async def lock_accepted_note_content_for_entity_mutation(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
) -> None:
    """Lock the source before entity mutation; see current_relation_generation_statement."""
    await session.scalar(
        select(NoteContent.entity_id)
        .where(
            NoteContent.project_id == project_id,
            NoteContent.entity_id == entity_id,
        )
        .with_for_update()
    )


async def lock_accepted_note_content_for_delete(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: RuntimeEntityId,
) -> None:
    """Compatibility name for the accepted-delete lock-order boundary."""
    await lock_accepted_note_content_for_entity_mutation(
        session,
        project_id=project_id,
        entity_id=entity_id,
    )


async def delete_accepted_note(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity: AcceptedNoteDeleteEntitySource | None,
    note_content: RuntimeDeletedNoteFileChecksumSource | None = None,
    repositories: AcceptedNoteWriteRepositories,
) -> RuntimeAcceptedNoteChange[dict[str, object]]:
    """Plan an accepted note delete and remove the entity when it exists."""
    accepted = plan_accepted_note_delete_change(
        project_id=project_id,
        entity=entity,
        note_content=note_content,
    )
    if entity is not None:
        if note_content is not None:
            # Relation publication locks NoteContent before its Entity foreign-key check.
            # Taking the same order here prevents delete/publication from holding opposite locks.
            await lock_accepted_note_content_for_delete(
                session,
                project_id=project_id,
                entity_id=entity.id,
            )
        # Capture surviving linkers before the entity delete: its SET NULL cascade
        # erases their relations' to_id, after which nothing identifies which
        # sources' search rows still name the deleted target (#1351).
        relation_cleanup_entity_ids = await relation_cleanup_sources_for_deleted_entity(
            session,
            project_id=project_id,
            entity_id=entity.id,
        )
        await delete_accepted_note_search_index(
            session,
            project_id=project_id,
            entity_id=entity.id,
            repositories=repositories,
        )
        await delete_accepted_note_vectors(
            session,
            project_id=project_id,
            entity_id=entity.id,
            repositories=repositories,
        )
        await session.delete(entity)
        accepted = replace(accepted, relation_cleanup_entity_ids=relation_cleanup_entity_ids)
    return accepted
