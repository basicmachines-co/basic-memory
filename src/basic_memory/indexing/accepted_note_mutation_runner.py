"""Portable orchestration for accepted note mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn, Protocol, cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.file_utils import ParseError
from basic_memory.indexing.accepted_note_write_runner import (
    AcceptedNoteCreatePreparer,
    AcceptedNoteEditPreparer,
    AcceptedNoteMovePreparer,
    AcceptedPreparedNoteWrite,
    AcceptedNoteReplacePreparer,
    AcceptedNoteWriteRepositories,
    create_accepted_pending_entity,
    delete_accepted_note,
    persist_accepted_note_write,
    prepare_accepted_note_create,
    prepare_accepted_note_edit,
    prepare_accepted_note_move,
    prepare_accepted_note_replace,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import NoteContentRepository
from basic_memory.services.exceptions import EntityAlreadyExistsError
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.runtime import (
    NoteExternalId,
    ProjectExternalId,
    ProjectId,
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteWriteConflictKind,
    RuntimeFilePath,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteContentResponsePayload,
    accepted_note_file_path_conflicts,
    classify_accepted_note_write_conflict,
    normalize_note_move_destination_path,
    plan_accepted_note_write_change,
    runtime_content_type_is_markdown,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest

type AcceptedNoteMutationChange = RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]
type AcceptedNoteMutationUserProfileId = UUID


class AcceptedNoteMutationRejectKind(StrEnum):
    """Portable accepted-note mutation rejection categories."""

    bad_request = "bad_request"
    conflict = "conflict"
    not_found = "not_found"
    unsupported_media_type = "unsupported_media_type"

    @property
    def http_status_code(self) -> int:
        """Return the route status that matches this rejection behavior."""
        match self:
            case AcceptedNoteMutationRejectKind.bad_request:
                return 400
            case AcceptedNoteMutationRejectKind.conflict:
                return 409
            case AcceptedNoteMutationRejectKind.not_found:
                return 404
            case AcceptedNoteMutationRejectKind.unsupported_media_type:
                return 415


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationRejection:
    """Typed rejection from accepted-note mutation orchestration."""

    kind: AcceptedNoteMutationRejectKind
    detail: str


class AcceptedNoteMutationRejected(Exception):
    """Exception wrapper for a typed accepted-note mutation rejection."""

    def __init__(self, rejection: AcceptedNoteMutationRejection) -> None:
        super().__init__(rejection.detail)
        self.rejection = rejection


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationActor:
    """Actor metadata attached to post-commit accepted-note follow-up work."""

    user_profile_id: AcceptedNoteMutationUserProfileId | None
    kind: RuntimeNoteActorKind | None = None
    name: RuntimeNoteActorName | None = None


@dataclass(frozen=True, slots=True)
class AcceptedNoteCreateMutation:
    """Input for accepting a newly-created markdown note."""

    project_external_id: ProjectExternalId
    data: EntitySchema
    actor: AcceptedNoteMutationActor
    source: RuntimeNoteChangeSource


@dataclass(frozen=True, slots=True)
class AcceptedNoteUpdateMutation:
    """Input for accepting a PUT create-or-replace markdown note."""

    project_external_id: ProjectExternalId
    entity_external_id: NoteExternalId
    data: EntitySchema
    actor: AcceptedNoteMutationActor
    source: RuntimeNoteChangeSource


@dataclass(frozen=True, slots=True)
class AcceptedNoteEditMutation:
    """Input for accepting a partial markdown note edit."""

    project_external_id: ProjectExternalId
    entity_external_id: NoteExternalId
    data: EditEntityRequest
    actor: AcceptedNoteMutationActor
    source: RuntimeNoteChangeSource


@dataclass(frozen=True, slots=True)
class AcceptedNoteMoveMutation:
    """Input for accepting a DB-first markdown note move."""

    project_external_id: ProjectExternalId
    entity_external_id: NoteExternalId
    destination_path: RuntimeFilePath
    actor: AcceptedNoteMutationActor
    source: RuntimeNoteChangeSource


@dataclass(frozen=True, slots=True)
class AcceptedNoteDeleteMutation:
    """Input for deleting one accepted note."""

    project_external_id: ProjectExternalId
    entity_external_id: NoteExternalId


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationMovePolicy:
    """Permalink policy for DB-first accepted note moves."""

    disable_permalinks: bool
    update_permalinks_on_move: bool

    def should_update_permalink(self, entity: Entity) -> bool:
        return not self.disable_permalinks and (
            self.update_permalinks_on_move or entity.permalink is None
        )


class AcceptedNoteMutationClock(Protocol):
    """Clock capability for accepted-note mutation timestamps."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemAcceptedNoteMutationClock:
    """UTC wall clock for accepted-note mutation timestamps."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class AcceptedNoteMutationProjectRepository(Protocol):
    """Project lookup capability for accepted-note mutations."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: ProjectExternalId,
    ) -> Project | None: ...


class AcceptedNoteMutationEntityRepository(Protocol):
    """Entity lookup capability for accepted-note mutations."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: NoteExternalId,
        *,
        load_relations: bool = False,
    ) -> Entity | None: ...

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: RuntimeFilePath,
        *,
        load_relations: bool = False,
    ) -> Entity | None: ...


class AcceptedNoteMutationNoteContentRepository(Protocol):
    """note_content lookup capability for accepted-note mutations."""

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> NoteContent | None: ...


class AcceptedNoteMutationPreparer(
    AcceptedNoteCreatePreparer,
    AcceptedNoteReplacePreparer,
    AcceptedNoteEditPreparer,
    AcceptedNoteMovePreparer,
    Protocol,
):
    """Combined Basic Memory prepare capability for accepted note mutations."""


class AcceptedNoteMutationPreparerFactory(Protocol):
    """Factory for Basic Memory prepare-only note semantics."""

    def create_note_preparer(self, project: Project) -> AcceptedNoteMutationPreparer: ...


class AcceptedNoteMutationRepositories(Protocol):
    """Repository lookup capability set for accepted-note mutation orchestration."""

    def entity_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteMutationEntityRepository: ...

    def note_content_repository(
        self,
        project_id: ProjectId,
    ) -> AcceptedNoteMutationNoteContentRepository: ...


class AcceptedNoteRepositories(
    AcceptedNoteMutationRepositories,
    AcceptedNoteWriteRepositories,
    Protocol,
):
    """Repository capability set for DB-first accepted-note mutations."""


@dataclass(frozen=True, slots=True)
class DefaultAcceptedNoteRepositories:
    """Default core repositories for accepted-note mutation orchestration."""

    def entity_repository(self, project_id: ProjectId) -> EntityRepository:
        return EntityRepository(project_id=project_id)

    def pending_entity_repository(self, project_id: ProjectId) -> EntityRepository:
        return EntityRepository(project_id=project_id)

    def note_content_repository(self, project_id: ProjectId) -> NoteContentRepository:
        return NoteContentRepository(project_id=project_id)

    def search_repository(self, project_id: ProjectId) -> AcceptedNoteSearchRepository:
        return AcceptedNoteSearchRepository(project_id=project_id)


def build_default_accepted_note_repositories() -> AcceptedNoteRepositories:
    """Compose default repositories for accepted-note mutation orchestration."""
    return DefaultAcceptedNoteRepositories()


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationDependencies:
    """Dependencies required by accepted-note mutation orchestration."""

    project_repository: AcceptedNoteMutationProjectRepository
    lookup_repositories: AcceptedNoteMutationRepositories
    preparer_factory: AcceptedNoteMutationPreparerFactory
    write_repositories: AcceptedNoteWriteRepositories
    clock: AcceptedNoteMutationClock
    move_policy: AcceptedNoteMutationMovePolicy
    # Trigger: local runtimes where the filesystem is the source of truth.
    # Why: a DB-first create over a file that exists on disk but is not yet
    #   indexed would commit new DB/search rows while the file keeps old content;
    #   the next watcher pass then overwrites the DB with the stale file content,
    #   silently losing the write. Cloud reconciles object storage during
    #   materialization, so it keeps DB-first acceptance.
    # Outcome: local creates reject the conflict up front (409) instead.
    verify_storage_absent_on_create: bool = False


def accepted_note_integrity_rejection(error: IntegrityError) -> AcceptedNoteMutationRejection:
    """Map repository integrity errors into portable accepted-note rejections."""
    conflict_kind = classify_accepted_note_write_conflict(str(error.orig or error))

    if conflict_kind is RuntimeAcceptedNoteWriteConflictKind.file_path:
        return AcceptedNoteMutationRejection(
            kind=AcceptedNoteMutationRejectKind.conflict,
            detail="Note already exists. Use edit_note to modify it, or delete it first.",
        )

    if conflict_kind is RuntimeAcceptedNoteWriteConflictKind.external_id:
        return AcceptedNoteMutationRejection(
            kind=AcceptedNoteMutationRejectKind.conflict,
            detail="A note with this external_id already exists.",
        )

    if conflict_kind is RuntimeAcceptedNoteWriteConflictKind.permalink:
        return AcceptedNoteMutationRejection(
            kind=AcceptedNoteMutationRejectKind.conflict,
            detail="A note with this permalink already exists.",
        )

    return AcceptedNoteMutationRejection(
        kind=AcceptedNoteMutationRejectKind.conflict,
        detail="The note could not be written because it conflicts with existing note state.",
    )


async def run_accepted_note_create(
    session: AsyncSession,
    *,
    request: AcceptedNoteCreateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    """Accept a new markdown note into DB state without materializing its file."""
    try:
        return await _run_accepted_note_create(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error


async def run_accepted_note_update(
    session: AsyncSession,
    *,
    request: AcceptedNoteUpdateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    """Accept a PUT create-or-replace into DB state without materializing its file."""
    try:
        return await _run_accepted_note_update(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error


async def run_accepted_note_edit(
    session: AsyncSession,
    *,
    request: AcceptedNoteEditMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    """Accept a partial note edit into DB state without materializing its file."""
    try:
        return await _run_accepted_note_edit(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error


async def run_accepted_note_move(
    session: AsyncSession,
    *,
    request: AcceptedNoteMoveMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    """Accept a note move into DB state without materializing its file."""
    try:
        return await _run_accepted_note_move(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error


async def run_accepted_note_delete(
    session: AsyncSession,
    *,
    request: AcceptedNoteDeleteMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    """Delete one accepted note and return any materialized-file cleanup."""
    project = await load_accepted_note_mutation_project(
        session,
        project_external_id=request.project_external_id,
        dependencies=dependencies,
    )
    entity_repository = dependencies.lookup_repositories.entity_repository(project.id)
    entity = await entity_repository.get_by_external_id(
        session,
        request.entity_external_id,
        load_relations=False,
    )
    if entity is None:
        return cast(
            AcceptedNoteMutationChange,
            await delete_accepted_note(
                session,
                project_id=project.id,
                entity=None,
            ),
        )

    note_content = await load_accepted_note_content(
        session,
        project_id=project.id,
        entity_id=entity.id,
        dependencies=dependencies,
        missing_kind=None,
    )
    accepted = await delete_accepted_note(
        session,
        project_id=project.id,
        entity=entity,
        note_content=note_content,
        repositories=dependencies.write_repositories,
    )
    return cast(AcceptedNoteMutationChange, accepted)


async def _run_accepted_note_create(
    session: AsyncSession,
    *,
    request: AcceptedNoteCreateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    ensure_accepted_note_markdown_entity(request.data)

    now = dependencies.clock.now()
    user_profile_value = (
        str(request.actor.user_profile_id) if request.actor.user_profile_id is not None else None
    )
    project = await load_accepted_note_mutation_project(
        session,
        project_external_id=request.project_external_id,
        dependencies=dependencies,
    )

    entity_repository = dependencies.lookup_repositories.entity_repository(project.id)
    conflicting_entity = await entity_repository.get_by_file_path(
        session,
        request.data.file_path,
        load_relations=False,
    )
    reject_accepted_note_file_path_conflict(
        conflicting_entity,
        allowed_entity_external_id="",
    )

    preparer = dependencies.preparer_factory.create_note_preparer(project)
    try:
        prepared_write = await prepare_accepted_note_create(
            preparer,
            request.data,
            check_storage_exists=dependencies.verify_storage_absent_on_create,
            session=session,
        )
    except EntityAlreadyExistsError as error:
        # An unindexed file already occupies this path (local source-of-truth
        # runtimes only). Reject instead of committing DB state that the next
        # watcher pass would overwrite with the stale file content.
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.conflict, str(error))
    except (ParseError, ValueError) as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    prepared = prepared_write.prepared
    entity = await create_accepted_pending_entity(
        session,
        prepared=prepared,
        project_id=project.id,
        now=now,
        user_profile_value=user_profile_value,
        repositories=dependencies.write_repositories,
    )
    persisted = await persist_accepted_note_write(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        db_checksum=prepared_write.db_checksum,
        search_content=prepared.search_content,
        last_source=request.source,
        updated_at=now,
        repositories=dependencies.write_repositories,
    )
    accepted = plan_accepted_note_write_change(
        status_code=201,
        entity=entity,
        note_content=persisted.note_content,
        actor_user_profile_id=request.actor.user_profile_id,
        actor_kind=request.actor.kind,
        actor_name=request.actor.name,
        fallback_source=request.source,
    )
    return cast(AcceptedNoteMutationChange, accepted)


async def _run_accepted_note_update(
    session: AsyncSession,
    *,
    request: AcceptedNoteUpdateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    ensure_accepted_note_markdown_entity(request.data)

    now = dependencies.clock.now()
    user_profile_value = (
        str(request.actor.user_profile_id) if request.actor.user_profile_id is not None else None
    )
    project = await load_accepted_note_mutation_project(
        session,
        project_external_id=request.project_external_id,
        dependencies=dependencies,
    )
    entity_repository = dependencies.lookup_repositories.entity_repository(project.id)
    entity = await entity_repository.get_by_external_id(
        session,
        request.entity_external_id,
        load_relations=False,
    )
    created = entity is None
    existing_file_path = entity.file_path if entity is not None else None

    await reject_conflicting_accepted_note_file_path(
        session,
        project_id=project.id,
        file_path=request.data.file_path,
        allowed_entity_external_id=request.entity_external_id,
        dependencies=dependencies,
    )

    preparer = dependencies.preparer_factory.create_note_preparer(project)
    if entity is None:
        prepared_write = await prepare_create_or_reject(
            preparer,
            request.data,
            check_storage_exists=dependencies.verify_storage_absent_on_create,
            session=session,
        )
        entity = await create_accepted_pending_entity(
            session,
            prepared=prepared_write.prepared,
            project_id=project.id,
            now=now,
            user_profile_value=user_profile_value,
            external_id=request.entity_external_id,
            repositories=dependencies.write_repositories,
        )
        current_note_content = None
    else:
        current_note_content = await load_required_accepted_note_content(
            session,
            project_id=project.id,
            entity_id=entity.id,
            dependencies=dependencies,
            missing_kind=AcceptedNoteMutationRejectKind.conflict,
        )
        try:
            prepared_write = await prepare_accepted_note_replace(
                preparer,
                session,
                entity=entity,
                data=request.data,
                current_note_content=current_note_content,
                now=now,
                user_profile_value=user_profile_value,
            )
        except (ParseError, ValueError) as error:
            reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    prepared = prepared_write.prepared
    persisted = await persist_accepted_note_write(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        db_checksum=prepared_write.db_checksum,
        search_content=prepared.search_content,
        last_source=request.source,
        updated_at=now,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=entity.file_path,
        repositories=dependencies.write_repositories,
    )
    accepted = plan_accepted_note_write_change(
        status_code=201 if created else 200,
        entity=entity,
        note_content=persisted.note_content,
        actor_user_profile_id=request.actor.user_profile_id,
        actor_kind=request.actor.kind,
        actor_name=request.actor.name,
        cleanup_after_write=persisted.previous_file_delete,
        fallback_source=request.source,
    )
    return cast(AcceptedNoteMutationChange, accepted)


async def _run_accepted_note_edit(
    session: AsyncSession,
    *,
    request: AcceptedNoteEditMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    now = dependencies.clock.now()
    user_profile_value = (
        str(request.actor.user_profile_id) if request.actor.user_profile_id is not None else None
    )
    project, entity, current_note_content = await load_existing_markdown_note_content(
        session,
        project_external_id=request.project_external_id,
        entity_external_id=request.entity_external_id,
        dependencies=dependencies,
    )
    preparer = dependencies.preparer_factory.create_note_preparer(project)
    try:
        prepared_write = await prepare_accepted_note_edit(
            preparer,
            session,
            entity=entity,
            current_note_content=current_note_content,
            operation=request.data.operation,
            content=request.data.content,
            section=request.data.section,
            find_text=request.data.find_text,
            expected_replacements=request.data.expected_replacements,
            now=now,
            user_profile_value=user_profile_value,
        )
    except (ParseError, ValueError) as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    prepared = prepared_write.prepared
    persisted = await persist_accepted_note_write(
        session,
        entity=entity,
        markdown_content=prepared.markdown_content,
        db_checksum=prepared_write.db_checksum,
        search_content=prepared.search_content,
        last_source=request.source,
        updated_at=now,
        current_note_content=current_note_content,
        accepted_file_path=entity.file_path,
        repositories=dependencies.write_repositories,
    )
    accepted = plan_accepted_note_write_change(
        status_code=200,
        entity=entity,
        note_content=persisted.note_content,
        actor_user_profile_id=request.actor.user_profile_id,
        actor_kind=request.actor.kind,
        actor_name=request.actor.name,
        fallback_source=request.source,
    )
    return cast(AcceptedNoteMutationChange, accepted)


async def _run_accepted_note_move(
    session: AsyncSession,
    *,
    request: AcceptedNoteMoveMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationChange:
    try:
        accepted_file_path = normalize_note_move_destination_path(
            request.destination_path
        ).file_path
    except ValueError as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    now = dependencies.clock.now()
    user_profile_value = (
        str(request.actor.user_profile_id) if request.actor.user_profile_id is not None else None
    )
    project, entity, current_note_content = await load_existing_markdown_note_content(
        session,
        project_external_id=request.project_external_id,
        entity_external_id=request.entity_external_id,
        dependencies=dependencies,
    )
    existing_file_path = entity.file_path
    if accepted_file_path == existing_file_path:
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.bad_request,
            "Source and destination paths are the same.",
        )

    await reject_conflicting_accepted_note_file_path(
        session,
        project_id=project.id,
        file_path=accepted_file_path,
        allowed_entity_external_id=request.entity_external_id,
        dependencies=dependencies,
    )
    should_update_permalink = dependencies.move_policy.should_update_permalink(entity)
    preparer = dependencies.preparer_factory.create_note_preparer(project)
    # Local source-of-truth guard: reject a move onto a destination file that exists
    # on disk but is not indexed (mirrors the create/PUT storage check) before
    # committing DB/search to the new path. Cloud is DB-first (flag is False).
    if dependencies.verify_storage_absent_on_create:
        try:
            await preparer.verify_move_destination_absent(
                source_file_path=entity.file_path,
                destination_file_path=accepted_file_path,
            )
        except EntityAlreadyExistsError as error:
            reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.conflict, str(error))
    try:
        prepared_move = await prepare_accepted_note_move(
            preparer if should_update_permalink else None,
            session,
            entity=entity,
            current_note_content=current_note_content,
            accepted_file_path=accepted_file_path,
            should_update_permalink=should_update_permalink,
            now=now,
            user_profile_value=user_profile_value,
        )
    except (ParseError, ValueError) as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    persisted = await persist_accepted_note_write(
        session,
        entity=entity,
        markdown_content=prepared_move.markdown_content,
        db_checksum=prepared_move.db_checksum,
        search_content=prepared_move.search_content,
        last_source=request.source,
        updated_at=now,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=prepared_move.file_path,
        repositories=dependencies.write_repositories,
    )
    accepted = plan_accepted_note_write_change(
        status_code=200,
        entity=entity,
        note_content=persisted.note_content,
        actor_user_profile_id=request.actor.user_profile_id,
        actor_kind=request.actor.kind,
        actor_name=request.actor.name,
        cleanup_after_write=persisted.previous_file_delete,
        fallback_source=request.source,
    )
    return cast(AcceptedNoteMutationChange, accepted)


async def load_accepted_note_mutation_project(
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    dependencies: AcceptedNoteMutationDependencies,
) -> Project:
    """Load the mutation project or reject the mutation."""
    project = await dependencies.project_repository.get_by_external_id(
        session,
        project_external_id,
    )
    if project is None:
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.not_found,
            f"Project '{project_external_id}' not found",
        )
    return project


async def load_existing_markdown_note_content(
    session: AsyncSession,
    *,
    project_external_id: ProjectExternalId,
    entity_external_id: NoteExternalId,
    dependencies: AcceptedNoteMutationDependencies,
) -> tuple[Project, Entity, NoteContent]:
    """Load an existing markdown note and its accepted DB content."""
    project = await load_accepted_note_mutation_project(
        session,
        project_external_id=project_external_id,
        dependencies=dependencies,
    )
    entity_repository = dependencies.lookup_repositories.entity_repository(project.id)
    entity = await entity_repository.get_by_external_id(
        session,
        entity_external_id,
        load_relations=False,
    )
    if entity is None:
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.not_found,
            f"Entity with external_id '{entity_external_id}' not found",
        )
    if not runtime_content_type_is_markdown(entity):
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.unsupported_media_type,
            "Only markdown note mutations are supported by the note-content path.",
        )
    note_content = await load_required_accepted_note_content(
        session,
        project_id=project.id,
        entity_id=entity.id,
        dependencies=dependencies,
        missing_kind=AcceptedNoteMutationRejectKind.conflict,
    )
    return project, entity, note_content


async def load_required_accepted_note_content(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: int,
    dependencies: AcceptedNoteMutationDependencies,
    missing_kind: AcceptedNoteMutationRejectKind,
) -> NoteContent:
    """Load required accepted DB note content or reject the mutation."""
    note_content = await load_accepted_note_content(
        session,
        project_id=project_id,
        entity_id=entity_id,
        dependencies=dependencies,
        missing_kind=None,
    )
    if note_content is None:
        reject_accepted_note_mutation(
            missing_kind,
            "Note content is not available for this note yet. Retry after backfill.",
        )
    return note_content


async def load_accepted_note_content(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    entity_id: int,
    dependencies: AcceptedNoteMutationDependencies,
    missing_kind: AcceptedNoteMutationRejectKind | None,
) -> NoteContent | None:
    """Load accepted DB note content or reject if required."""
    repository = dependencies.lookup_repositories.note_content_repository(project_id)
    note_content = await repository.get_by_entity_id(session, entity_id)
    if note_content is None and missing_kind is not None:
        reject_accepted_note_mutation(
            missing_kind,
            "Note content is not available for this note yet. Retry after backfill.",
        )
    return note_content


async def reject_conflicting_accepted_note_file_path(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    file_path: RuntimeFilePath,
    allowed_entity_external_id: NoteExternalId,
    dependencies: AcceptedNoteMutationDependencies,
) -> None:
    """Reject target file paths that already belong to another entity."""
    entity_repository = dependencies.lookup_repositories.entity_repository(project_id)
    conflicting_entity = await entity_repository.get_by_file_path(
        session,
        file_path,
        load_relations=False,
    )
    reject_accepted_note_file_path_conflict(
        conflicting_entity,
        allowed_entity_external_id=allowed_entity_external_id,
    )


async def prepare_create_or_reject(
    preparer: AcceptedNoteCreatePreparer,
    data: EntitySchema,
    *,
    check_storage_exists: bool,
    session: AsyncSession,
) -> AcceptedPreparedNoteWrite:
    """Prepare a new accepted note or raise a typed mutation rejection."""
    try:
        return await prepare_accepted_note_create(
            preparer,
            data,
            check_storage_exists=check_storage_exists,
            session=session,
        )
    except EntityAlreadyExistsError as error:
        # PUT-as-create over an unindexed on-disk file (local source-of-truth
        # runtimes). Reject rather than committing divergent DB state.
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.conflict, str(error))
    except (ParseError, ValueError) as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))


def ensure_accepted_note_markdown_entity(data: EntitySchema) -> None:
    """Reject non-markdown note mutations before orchestration starts."""
    if not runtime_content_type_is_markdown(data):
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.unsupported_media_type,
            "Only markdown note writes are supported by the note-content path.",
        )


def reject_accepted_note_file_path_conflict(
    conflicting_entity: Entity | None,
    *,
    allowed_entity_external_id: NoteExternalId,
) -> None:
    """Reject an accepted-note path conflict."""
    if accepted_note_file_path_conflicts(
        conflicting_entity,
        allowed_entity_external_id=allowed_entity_external_id,
    ):
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.conflict,
            "Note already exists. Use edit_note to modify it, or delete it first.",
        )


def reject_accepted_note_mutation(
    kind: AcceptedNoteMutationRejectKind,
    detail: str,
) -> NoReturn:
    """Raise one typed accepted-note mutation rejection."""
    raise AcceptedNoteMutationRejected(
        AcceptedNoteMutationRejection(
            kind=kind,
            detail=detail,
        )
    )
