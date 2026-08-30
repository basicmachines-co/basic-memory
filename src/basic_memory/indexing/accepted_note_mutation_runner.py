"""Portable orchestration for accepted note mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn, Protocol
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
    AcceptedNoteSelfRelationResolver,
    AcceptedNoteWriteRepositories,
    create_accepted_pending_entity,
    delete_accepted_note,
    lock_accepted_note_content_for_entity_mutation,
    persist_accepted_note_move,
    persist_accepted_note_snapshot,
    prepare_accepted_note_create,
    prepare_accepted_note_edit,
    prepare_accepted_note_move,
    prepare_accepted_note_replace,
)
from basic_memory.indexing.relation_persistence import RelationGenerationPublication
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import NoteContentVersionConflict
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository
from basic_memory.services.exceptions import EntityAlreadyExistsError
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteWriteConflictKind,
    RuntimeNoteContentResponsePayload,
    accepted_note_file_path_conflicts,
    classify_accepted_note_write_conflict,
    plan_accepted_note_write_change,
    select_accepted_note_source_checksum,
)
from basic_memory.runtime.note_move import normalize_note_move_destination_path
from basic_memory.runtime.note_object_metadata import NOTE_SOURCE_COLLABORATION_RELAY
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)
from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectExternalId,
    ProjectId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    runtime_content_type_is_markdown,
    runtime_file_path_is_markdown_note,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.utils import resolve_directory_casing

type AcceptedNoteMutationChange = RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]
type AcceptedNoteMutationUserProfileId = UUID
ACCEPTED_NOTE_DELETE_SOURCE: RuntimeNoteChangeSource = "delete_note"


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
class AcceptedNoteBaseChecksumConflict:
    """Structured 409 detail for a failed base-checksum precondition.

    Browser saves and the collaboration relay parse exactly this wire shape —
    {"message": ..., "db_checksum": ...} — to decide whether to rebase against
    the current accepted content (checksum present) or treat the note as gone
    (checksum null). Keep the message text and key names stable (issue #1445).
    """

    db_checksum: str | None
    message: str = "Note changed since your last sync"

    def as_json_dict(self) -> dict[str, str | None]:
        """Serialize to the wire shape route adapters place in the HTTP body."""
        return {"message": self.message, "db_checksum": self.db_checksum}


type AcceptedNoteMutationRejectionDetail = str | AcceptedNoteBaseChecksumConflict


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationRejection:
    """Typed rejection from accepted-note mutation orchestration."""

    kind: AcceptedNoteMutationRejectKind
    detail: AcceptedNoteMutationRejectionDetail


class AcceptedNoteMutationRejected(Exception):
    """Exception wrapper for a typed accepted-note mutation rejection."""

    def __init__(self, rejection: AcceptedNoteMutationRejection) -> None:
        super().__init__(str(rejection.detail))
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
    publish_graph_facts: bool = True


@dataclass(frozen=True, slots=True)
class AcceptedNoteUpdateMutation:
    """Input for accepting a PUT create-or-replace markdown note."""

    project_external_id: ProjectExternalId
    entity_external_id: NoteExternalId
    data: EntitySchema
    actor: AcceptedNoteMutationActor
    source: RuntimeNoteChangeSource
    # db_checksum the caller last synced; None means no precondition (issue #1445).
    base_checksum: str | None = None
    publish_graph_facts: bool = True


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


def accepted_note_mutation_utc_now() -> datetime:
    """Return the current UTC time used to stamp accepted-note mutations."""
    return datetime.now(tz=UTC)


class AcceptedNoteMutationProjectRepository(Protocol):
    """Project lookup capability for accepted-note mutations."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: ProjectExternalId,
    ) -> Project | None: ...

    async def advance_partition_position(
        self,
        session: AsyncSession,
        project_id: ProjectId,
    ) -> int: ...

    async def record_accepted_note_change(
        self,
        session: AsyncSession,
        change: RuntimeAcceptedProjectNoteChange,
    ) -> None: ...


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

    async def get_distinct_directories(
        self,
        session: AsyncSession,
    ) -> list[str]: ...


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
    AcceptedNoteSelfRelationResolver,
    Protocol,
):
    """Combined Basic Memory prepare capability for accepted note mutations."""

    async def detect_file_path_conflicts(
        self,
        file_path: RuntimeFilePath,
        skip_check: bool = ...,
        session: AsyncSession | None = ...,
    ) -> list[str]: ...


class AcceptedNoteMutationPreparerFactory(Protocol):
    """Factory for Basic Memory prepare-only note semantics."""

    def create_note_preparer(self, project: Project) -> AcceptedNoteMutationPreparer: ...

    async def load_current_file_checksum(
        self,
        project: Project,
        file_path: RuntimeFilePath,
    ) -> RuntimeFileChecksum | None: ...


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


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationDependencies:
    """Dependencies required by accepted-note mutation orchestration."""

    project_repository: AcceptedNoteMutationProjectRepository
    lookup_repositories: AcceptedNoteMutationRepositories
    preparer_factory: AcceptedNoteMutationPreparerFactory
    write_repositories: AcceptedNoteWriteRepositories
    move_policy: AcceptedNoteMutationMovePolicy
    # Trigger: local runtimes where the filesystem is the source of truth.
    # Why: a DB-first create over a file that exists on disk but is not yet
    #   indexed would commit new DB/search rows while the file keeps old content;
    #   the next watcher pass then overwrites the DB with the stale file content,
    #   silently losing the write. Cloud reconciles object storage during
    #   materialization, so it keeps DB-first acceptance.
    # Outcome: local creates reject the conflict up front (409) instead.
    verify_storage_absent_on_create: bool = False


@dataclass(frozen=True, slots=True)
class AcceptedNoteMutationResult:
    """Accepted response plus relation work that must run after commit."""

    change: AcceptedNoteMutationChange
    relation_publication: RelationGenerationPublication | None = None


async def record_accepted_project_note_change(
    session: AsyncSession,
    *,
    project: Project,
    entity: Entity,
    operation: RuntimeProjectNoteOperation,
    accepted_at: datetime,
    source: RuntimeNoteChangeSource,
    previous_file_path: RuntimeFilePath | None,
    note_content: NoteContent | None,
    actor: AcceptedNoteMutationActor | None,
    dependencies: AcceptedNoteMutationDependencies,
) -> RuntimeAcceptedProjectNoteChange:
    """Claim and describe one accepted change in the project's strict partition."""
    if entity.permalink is None:
        raise RuntimeError(
            f"Accepted note is missing permalink for entity_id={entity.id}"
        )
    position = await dependencies.project_repository.advance_partition_position(
        session,
        project.id,
    )
    change = RuntimeAcceptedProjectNoteChange(
        project_id=project.id,
        project_external_id=project.external_id,
        partition_position=position,
        entity_id=entity.id,
        note_external_id=entity.external_id,
        permalink=entity.permalink,
        title=entity.title,
        operation=operation,
        file_path=entity.file_path,
        previous_file_path=previous_file_path,
        accepted_at=accepted_at,
        source=source,
        db_version=note_content.db_version if note_content is not None else None,
        db_checksum=note_content.db_checksum if note_content is not None else None,
        actor_user_profile_id=actor.user_profile_id if actor is not None else None,
        actor_kind=actor.kind if actor is not None else None,
        actor_name=actor.name if actor is not None else None,
    )
    await dependencies.project_repository.record_accepted_note_change(session, change)
    return change


def attach_accepted_project_note_change(
    change: AcceptedNoteMutationChange,
    project_change: RuntimeAcceptedProjectNoteChange,
) -> AcceptedNoteMutationChange:
    """Carry accepted partition evidence through existing runtime follow-up work."""
    materialization = (
        replace(change.materialization, project_change=project_change)
        if change.materialization is not None
        else None
    )
    file_delete = (
        replace(change.file_delete, project_change=project_change)
        if change.file_delete is not None
        else None
    )
    return replace(
        change,
        project_change=project_change,
        materialization=materialization,
        file_delete=file_delete,
    )


def apply_accepted_note_graph_policy(
    prepared_write: AcceptedPreparedNoteWrite,
    *,
    publish_graph_facts: bool,
) -> AcceptedPreparedNoteWrite:
    """Keep canonical Markdown while suppressing graph facts for derived documents."""
    if publish_graph_facts:
        return prepared_write
    prepared = prepared_write.prepared
    graph_silent_markdown = prepared.entity_markdown.model_copy(
        update={"observations": [], "relations": []}
    )
    return replace(
        prepared_write,
        prepared=replace(prepared, entity_markdown=graph_silent_markdown),
    )


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


def concurrent_write_rejection() -> AcceptedNoteMutationRejection:
    """Rejection for an accepted write that lost an optimistic-concurrency race."""
    return AcceptedNoteMutationRejection(
        kind=AcceptedNoteMutationRejectKind.conflict,
        detail="The note was modified concurrently. Reload the latest content and retry.",
    )


def reject_stale_base_checksum(current_db_checksum: str | None) -> NoReturn:
    """Reject a PUT whose base-checksum precondition no longer matches DB state."""
    raise AcceptedNoteMutationRejected(
        AcceptedNoteMutationRejection(
            kind=AcceptedNoteMutationRejectKind.conflict,
            detail=AcceptedNoteBaseChecksumConflict(db_checksum=current_db_checksum),
        )
    )


async def resolve_accepted_note_source_checksum(
    *,
    project: Project,
    file_path: RuntimeFilePath,
    current_note_content: NoteContent,
    preparer_factory: AcceptedNoteMutationPreparerFactory,
) -> RuntimeFileChecksum | None:
    """Resolve source bytes only when current storage proves ownership."""
    observed_file_checksum = await preparer_factory.load_current_file_checksum(
        project,
        file_path,
    )
    return select_accepted_note_source_checksum(
        current_note_content,
        observed_file_checksum=observed_file_checksum,
    )


async def resolve_accepted_note_directory(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    directory: str,
    dependencies: AcceptedNoteMutationDependencies,
) -> str:
    """Resolve a requested note directory against existing folder casing (#1326).

    Trigger: the requested directory is not an existing folder but matches
        exactly one existing folder case-insensitively.
    Why: LLM callers guess plausible casing ("schemas" beside an existing
        "Schemas/"); on case-sensitive storage (cloud object storage) the guess
        silently creates a case-duplicate sibling folder. Folders are derived
        from indexed entity file paths in the DB, never by probing storage, so
        local and cloud runtimes resolve identically.
    Outcome: a unique case-insensitive match adopts the existing folder's
        casing; exact matches, unknown folders, and ambiguous case-variant
        siblings keep the requested casing unchanged.
    """
    if not directory:
        return directory
    entity_repository = dependencies.lookup_repositories.entity_repository(project_id)
    existing_directories = await entity_repository.get_distinct_directories(session)
    return resolve_directory_casing(directory, existing_directories)


async def resolve_accepted_note_schema_directory(
    session: AsyncSession,
    *,
    project_id: ProjectId,
    data: EntitySchema,
    dependencies: AcceptedNoteMutationDependencies,
) -> EntitySchema:
    """Return the write schema with its directory resolved to existing casing.

    The schema's ``file_path`` is computed from ``directory``, so adjusting the
    directory redirects the conflict lookup, preparation, and permalink
    resolution downstream. The caller's schema is never mutated: a resolved
    directory yields a copy so route-owned request data stays as received.
    """
    resolved_directory = await resolve_accepted_note_directory(
        session,
        project_id=project_id,
        directory=data.directory,
        dependencies=dependencies,
    )
    if resolved_directory == data.directory:
        return data
    return data.model_copy(update={"directory": resolved_directory})


async def run_accepted_note_create(
    session: AsyncSession,
    *,
    request: AcceptedNoteCreateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
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
) -> AcceptedNoteMutationResult:
    """Accept a PUT create-or-replace into DB state without materializing its file."""
    try:
        return await _run_accepted_note_update(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error
    except NoteContentVersionConflict as error:
        raise AcceptedNoteMutationRejected(concurrent_write_rejection()) from error


async def run_accepted_note_edit(
    session: AsyncSession,
    *,
    request: AcceptedNoteEditMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
    """Accept a partial note edit into DB state without materializing its file."""
    try:
        return await _run_accepted_note_edit(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error
    except NoteContentVersionConflict as error:
        raise AcceptedNoteMutationRejected(concurrent_write_rejection()) from error


async def run_accepted_note_move(
    session: AsyncSession,
    *,
    request: AcceptedNoteMoveMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
    """Accept a note move into DB state without materializing its file."""
    try:
        return await _run_accepted_note_move(session, request=request, dependencies=dependencies)
    except IntegrityError as error:
        raise AcceptedNoteMutationRejected(accepted_note_integrity_rejection(error)) from error
    except NoteContentVersionConflict as error:
        raise AcceptedNoteMutationRejected(concurrent_write_rejection()) from error


async def run_accepted_note_delete(
    session: AsyncSession,
    *,
    request: AcceptedNoteDeleteMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
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
        return AcceptedNoteMutationResult(
            change=await delete_accepted_note(
                session,
                project_id=project.id,
                entity=None,
                repositories=dependencies.write_repositories,
            )
        )

    # The entity lookup above is intentionally unlocked so an already-missing
    # delete stays idempotent. Once the note exists, claim its mutation lock and
    # reload it before deleting: another delete may have removed the row while we
    # waited, while an update or move may have changed its accepted evidence.
    await lock_accepted_note_content_for_entity_mutation(
        session,
        project_id=project.id,
        entity_id=entity.id,
    )
    entity = await entity_repository.get_by_external_id(
        session,
        request.entity_external_id,
        load_relations=False,
    )
    if entity is None:
        return AcceptedNoteMutationResult(
            change=await delete_accepted_note(
                session,
                project_id=project.id,
                entity=None,
                repositories=dependencies.write_repositories,
            )
        )

    await session.refresh(entity)
    note_content = await load_accepted_note_content(
        session,
        project_id=project.id,
        entity_id=entity.id,
        dependencies=dependencies,
        missing_kind=None,
    )
    if note_content is not None:
        await session.refresh(note_content)
    change = await delete_accepted_note(
        session,
        project_id=project.id,
        entity=entity,
        note_content=note_content,
        repositories=dependencies.write_repositories,
    )
    # The legacy entity-delete route also removes binary resources. Keep that
    # behavior, but do not claim an accepted-note partition position for data
    # that cannot participate in Markdown indexing or Wiki projection.
    if not runtime_content_type_is_markdown(entity):
        return AcceptedNoteMutationResult(change=change)
    project_change = await record_accepted_project_note_change(
        session,
        project=project,
        entity=entity,
        operation=RuntimeProjectNoteOperation.deleted,
        accepted_at=accepted_note_mutation_utc_now(),
        source=ACCEPTED_NOTE_DELETE_SOURCE,
        previous_file_path=None,
        note_content=note_content,
        actor=None,
        dependencies=dependencies,
    )
    return AcceptedNoteMutationResult(
        change=attach_accepted_project_note_change(change, project_change)
    )


async def _run_accepted_note_create(
    session: AsyncSession,
    *,
    request: AcceptedNoteCreateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
    ensure_accepted_note_markdown_entity(request.data)

    now = accepted_note_mutation_utc_now()
    user_profile_value = (
        str(request.actor.user_profile_id) if request.actor.user_profile_id is not None else None
    )
    project = await load_accepted_note_mutation_project(
        session,
        project_external_id=request.project_external_id,
        dependencies=dependencies,
    )

    data = await resolve_accepted_note_schema_directory(
        session,
        project_id=project.id,
        data=request.data,
        dependencies=dependencies,
    )
    entity_repository = dependencies.lookup_repositories.entity_repository(project.id)
    conflicting_entity = await entity_repository.get_by_file_path(
        session,
        data.file_path,
        load_relations=False,
    )
    reject_accepted_note_file_path_conflict(
        conflicting_entity,
        allowed_entity_external_id="",
    )

    preparer = dependencies.preparer_factory.create_note_preparer(project)
    prepared_write = await prepare_create_or_reject(
        preparer,
        data,
        check_storage_exists=dependencies.verify_storage_absent_on_create,
        session=session,
    )
    prepared_write = apply_accepted_note_graph_policy(
        prepared_write,
        publish_graph_facts=request.publish_graph_facts,
    )
    prepared = prepared_write.prepared
    entity = await create_accepted_pending_entity(
        session,
        prepared=prepared,
        project_id=project.id,
        user_profile_value=user_profile_value,
        repositories=dependencies.write_repositories,
    )
    persisted = await persist_accepted_note_snapshot(
        session,
        entity=entity,
        prepared=prepared,
        db_checksum=prepared_write.db_checksum,
        last_source=request.source,
        updated_at=now,
        self_relation_resolver=preparer,
        repositories=dependencies.write_repositories,
    )
    project_change = await record_accepted_project_note_change(
        session,
        project=project,
        entity=entity,
        operation=RuntimeProjectNoteOperation.created,
        accepted_at=now,
        source=request.source,
        previous_file_path=None,
        note_content=persisted.note_content,
        actor=request.actor,
        dependencies=dependencies,
    )
    return AcceptedNoteMutationResult(
        change=attach_accepted_project_note_change(
            plan_accepted_note_write_change(
                status_code=201,
                entity=entity,
                note_content=persisted.note_content,
                actor_user_profile_id=request.actor.user_profile_id,
                actor_kind=request.actor.kind,
                actor_name=request.actor.name,
                fallback_source=request.source,
            ),
            project_change,
        ),
        relation_publication=persisted.relation_publication,
    )


async def _run_accepted_note_update(
    session: AsyncSession,
    *,
    request: AcceptedNoteUpdateMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
    ensure_accepted_note_markdown_entity(request.data)

    now = accepted_note_mutation_utc_now()
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
    current_note_content: NoteContent | None = None

    if entity is not None:
        # The first lookup resolves the addressed identity, but it is not a
        # stable source-path snapshot. Claim the note lock, then refresh the
        # entity before any path-sensitive preparation: a concurrent move may
        # have changed the accepted predecessor while this PUT was waiting.
        if not runtime_content_type_is_markdown(entity):
            reject_accepted_note_mutation(
                AcceptedNoteMutationRejectKind.unsupported_media_type,
                "Only markdown note mutations are supported by the note-content path.",
            )
        current_note_content = await load_required_accepted_note_content(
            session,
            project_id=project.id,
            entity_id=entity.id,
            dependencies=dependencies,
            missing_kind=AcceptedNoteMutationRejectKind.conflict,
        )
        await session.refresh(entity)
        if not runtime_content_type_is_markdown(entity):
            reject_accepted_note_mutation(
                AcceptedNoteMutationRejectKind.unsupported_media_type,
                "Only markdown note mutations are supported by the note-content path.",
            )

    existing_file_path = entity.file_path if entity is not None else None
    vacated_source: tuple[RuntimeFilePath, RuntimeFileChecksum | None] | None = None

    # Trigger: the addressed entity already lives in the exact requested directory.
    # Why: casing resolution scans every distinct entity file_path in the project,
    #     and content-only PUTs (e.g. repeated collaboration-relay saves of the
    #     same note) are the hot path where the directory cannot change; an exact
    #     match would resolve to itself anyway because exact match always wins.
    # Outcome: only case-variant or relocating PUTs pay for the directory scan.
    current_directory = existing_file_path.rpartition("/")[0] if existing_file_path else None
    if current_directory == request.data.directory:
        data = request.data
    else:
        data = await resolve_accepted_note_schema_directory(
            session,
            project_id=project.id,
            data=request.data,
            dependencies=dependencies,
        )
    await reject_conflicting_accepted_note_file_path(
        session,
        project_id=project.id,
        file_path=data.file_path,
        allowed_entity_external_id=request.entity_external_id,
        dependencies=dependencies,
    )

    preparer = dependencies.preparer_factory.create_note_preparer(project)
    if entity is None:
        # Trigger: the caller sent a base_checksum but the addressed entity is gone.
        # Why: a base_checksum means the caller synced this note and expects to
        #   replace it; the entity vanishing after that pre-read means it was
        #   deleted, and creating it here would silently resurrect the just-deleted
        #   note behind the user's back (issue #1445).
        # Outcome: structured 409 with db_checksum None — the note is gone, so
        #   there is nothing to rebase against.
        if request.base_checksum is not None:
            reject_stale_base_checksum(current_db_checksum=None)
        prepared_write = await prepare_create_or_reject(
            preparer,
            data,
            check_storage_exists=dependencies.verify_storage_absent_on_create,
            session=session,
        )
        entity = await create_accepted_pending_entity(
            session,
            prepared=prepared_write.prepared,
            project_id=project.id,
            user_profile_value=user_profile_value,
            external_id=request.entity_external_id,
            repositories=dependencies.write_repositories,
        )
        current_note_content = None
    else:
        # Local source-of-truth guard: a PUT that renames onto a destination file that
        # exists on disk but is not yet indexed would overwrite/lose that unindexed
        # write. Mirror the create/move storage check before committing DB/search to
        # the new path. Cloud is DB-first (flag is False) and reconciles storage later.
        if dependencies.verify_storage_absent_on_create:
            try:
                await preparer.verify_move_destination_absent(
                    source_file_path=entity.file_path,
                    destination_file_path=data.file_path,
                )
            except EntityAlreadyExistsError as error:
                reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.conflict, str(error))
        assert current_note_content is not None
        # A PUT replacement may also rename the note. Capture the exact source bytes before
        # persistence mutates the entity and note_content to the destination version so delayed
        # cleanup cannot let a later project index recreate the old path as a ghost.
        if data.file_path != entity.file_path:
            vacated_source = (
                entity.file_path,
                await resolve_accepted_note_source_checksum(
                    project=project,
                    file_path=entity.file_path,
                    current_note_content=current_note_content,
                    preparer_factory=dependencies.preparer_factory,
                ),
            )
        # Optimistic-concurrency precondition: the caller sent the db_checksum it
        # last synced; if the accepted row has advanced to a different write,
        # reject with the current checksum so the client rebases instead of
        # clobbering the newer write (issue #1445). The lock order is defined by
        # current_relation_generation_statement; accept_write's compare-and-set
        # remains the portable stale-write guard.
        if (
            request.base_checksum is not None
            and current_note_content.db_checksum != request.base_checksum
        ):
            # Hot-doc canonical (#1589 Phase G): while a live session exists the
            # Y.Doc is canonical, so a relay persist supersedes the current
            # head. The invariant that makes this safe is "the superseded
            # version survives as a storage object version", so a FOREIGN head
            # may only be superseded once it is provably IN storage: 'synced'
            # and nothing else. 'external_change_detected' explicitly means the
            # accepted DB markdown did NOT materialize (the guard protected an
            # unexpected external file), and pending/writing/failed heads have
            # no object version yet — superseding any of them would erase the
            # only copy, because their queued materialization preflights as
            # stale and never writes (Codex review, PR #1146). Rejecting keeps
            # the relay's next store retrying (seconds) until materialization
            # lands. Relay-over-relay stays unconditional: the live Y.Doc is
            # the merge of everything the relay ever persisted, which is what
            # closes the lost-ack wedge (2026-07-23 production incident).
            # Non-relay writers keep the full guarded semantics; the
            # deleted-entity 409 above and the db_version CAS both remain.
            current_head_in_storage = current_note_content.file_write_status == "synced"
            relay_supersede = request.source == NOTE_SOURCE_COLLABORATION_RELAY and (
                current_note_content.last_source == NOTE_SOURCE_COLLABORATION_RELAY
                or current_head_in_storage
            )
            if not relay_supersede:
                reject_stale_base_checksum(current_db_checksum=current_note_content.db_checksum)
        try:
            prepared_write = await prepare_accepted_note_replace(
                preparer,
                session,
                entity=entity,
                data=data,
                current_note_content=current_note_content,
                user_profile_value=user_profile_value,
            )
        except (ParseError, ValueError) as error:
            reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    prepared_write = apply_accepted_note_graph_policy(
        prepared_write,
        publish_graph_facts=request.publish_graph_facts,
    )
    prepared = prepared_write.prepared
    persisted = await persist_accepted_note_snapshot(
        session,
        entity=entity,
        prepared=prepared,
        db_checksum=prepared_write.db_checksum,
        last_source=request.source,
        updated_at=now,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        accepted_file_path=entity.file_path,
        source_file_checksum=vacated_source[1] if vacated_source is not None else None,
        self_relation_resolver=preparer,
        repositories=dependencies.write_repositories,
    )
    if (
        vacated_source is not None
        and vacated_source[1] is not None
        and entity.file_path != vacated_source[0]
    ):
        await NoteFileVacateRepository(project.id).record_vacate(
            session,
            entity_id=entity.id,
            file_path=vacated_source[0],
            file_checksum=vacated_source[1],
        )
    operation = (
        RuntimeProjectNoteOperation.created
        if created
        else (
            RuntimeProjectNoteOperation.moved
            if existing_file_path != entity.file_path
            else RuntimeProjectNoteOperation.updated
        )
    )
    previous_file_path = (
        existing_file_path if operation == RuntimeProjectNoteOperation.moved else None
    )
    project_change = await record_accepted_project_note_change(
        session,
        project=project,
        entity=entity,
        operation=operation,
        accepted_at=now,
        source=request.source,
        previous_file_path=previous_file_path,
        note_content=persisted.note_content,
        actor=request.actor,
        dependencies=dependencies,
    )
    return AcceptedNoteMutationResult(
        change=attach_accepted_project_note_change(
            plan_accepted_note_write_change(
                status_code=201 if created else 200,
                entity=entity,
                note_content=persisted.note_content,
                actor_user_profile_id=request.actor.user_profile_id,
                actor_kind=request.actor.kind,
                actor_name=request.actor.name,
                cleanup_after_write=persisted.previous_file_delete,
                fallback_source=request.source,
            ),
            project_change,
        ),
        relation_publication=persisted.relation_publication,
    )


async def _run_accepted_note_edit(
    session: AsyncSession,
    *,
    request: AcceptedNoteEditMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
    now = accepted_note_mutation_utc_now()
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
            replace_subsections=request.data.replace_subsections,
            user_profile_value=user_profile_value,
            metadata=request.data.metadata,
        )
    except (ParseError, ValueError) as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    prepared = prepared_write.prepared
    persisted = await persist_accepted_note_snapshot(
        session,
        entity=entity,
        prepared=prepared,
        db_checksum=prepared_write.db_checksum,
        last_source=request.source,
        updated_at=now,
        current_note_content=current_note_content,
        accepted_file_path=entity.file_path,
        self_relation_resolver=preparer,
        repositories=dependencies.write_repositories,
    )
    project_change = await record_accepted_project_note_change(
        session,
        project=project,
        entity=entity,
        operation=RuntimeProjectNoteOperation.updated,
        accepted_at=now,
        source=request.source,
        previous_file_path=None,
        note_content=persisted.note_content,
        actor=request.actor,
        dependencies=dependencies,
    )
    return AcceptedNoteMutationResult(
        change=attach_accepted_project_note_change(
            plan_accepted_note_write_change(
                status_code=200,
                entity=entity,
                note_content=persisted.note_content,
                actor_user_profile_id=request.actor.user_profile_id,
                actor_kind=request.actor.kind,
                actor_name=request.actor.name,
                fallback_source=request.source,
            ),
            project_change,
        ),
        relation_publication=persisted.relation_publication,
    )


async def _run_accepted_note_move(
    session: AsyncSession,
    *,
    request: AcceptedNoteMoveMutation,
    dependencies: AcceptedNoteMutationDependencies,
) -> AcceptedNoteMutationResult:
    try:
        accepted_file_path = normalize_note_move_destination_path(request.destination_path)
    except ValueError as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    now = accepted_note_mutation_utc_now()
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
    # The destination filename keeps its requested casing; only the parent
    # directory resolves against existing folders (issue #1326). The path is
    # posix-normalized above, so rpartition splits directory from filename.
    destination_directory, _, destination_filename = accepted_file_path.rpartition("/")
    resolved_directory = await resolve_accepted_note_directory(
        session,
        project_id=project.id,
        directory=destination_directory,
        dependencies=dependencies,
    )
    if resolved_directory != destination_directory:
        accepted_file_path = f"{resolved_directory}/{destination_filename}"
    # Same-path moves fail fast everywhere by decision (2026-07-14): cloud's
    # pre-unification route returned a 200 no-op, local rejected — the unified
    # runner keeps the rejection so a mistaken identity move surfaces instead
    # of silently acking.
    if accepted_file_path == existing_file_path:
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.bad_request,
            "Source and destination paths are the same.",
        )

    # Capture the source checksum before persisting the move mutates note_content to the
    # destination version. Observe storage for every publication state: even a synchronized DB
    # row can outlive a locally deleted source, and stale DB state must not authorize cleanup of a
    # byte-identical file recreated at that path later.
    vacated_source_checksum = await resolve_accepted_note_source_checksum(
        project=project,
        file_path=existing_file_path,
        current_note_content=current_note_content,
        preparer_factory=dependencies.preparer_factory,
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
            preparer,
            session,
            entity=entity,
            current_note_content=current_note_content,
            accepted_file_path=accepted_file_path,
            should_update_permalink=should_update_permalink,
            user_profile_value=user_profile_value,
        )
    except (ParseError, ValueError) as error:
        reject_accepted_note_mutation(AcceptedNoteMutationRejectKind.bad_request, str(error))

    persisted = await persist_accepted_note_move(
        session,
        entity=entity,
        prepared=prepared_move,
        last_source=request.source,
        updated_at=now,
        current_note_content=current_note_content,
        existing_file_path=existing_file_path,
        source_file_checksum=vacated_source_checksum,
        self_relation_resolver=preparer,
        repositories=dependencies.write_repositories,
    )
    # Trigger: storage confirms which source bytes this move vacated.
    # Why: an absent source is not evidence that the accepted DB checksum owns that path; recording
    # it could let delayed cleanup delete a legitimate byte-identical file created there later.
    # Outcome: only confirmed source objects receive the durable orphan gate and guarded cleanup.
    if vacated_source_checksum is not None:
        await NoteFileVacateRepository(project.id).record_vacate(
            session,
            entity_id=entity.id,
            file_path=existing_file_path,
            file_checksum=vacated_source_checksum,
        )
    project_change = await record_accepted_project_note_change(
        session,
        project=project,
        entity=entity,
        operation=RuntimeProjectNoteOperation.moved,
        accepted_at=now,
        source=request.source,
        previous_file_path=existing_file_path,
        note_content=persisted.note_content,
        actor=request.actor,
        dependencies=dependencies,
    )
    return AcceptedNoteMutationResult(
        change=attach_accepted_project_note_change(
            plan_accepted_note_write_change(
                status_code=200,
                entity=entity,
                note_content=persisted.note_content,
                actor_user_profile_id=request.actor.user_profile_id,
                actor_kind=request.actor.kind,
                actor_name=request.actor.name,
                previous_file_path=existing_file_path,
                cleanup_after_write=persisted.previous_file_delete,
                fallback_source=request.source,
            ),
            project_change,
        ),
        relation_publication=persisted.relation_publication,
    )


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
    # Claim the source before preparation; current_relation_generation_statement
    # is the canonical authority for the cross-table lock order.
    await lock_accepted_note_content_for_entity_mutation(
        session,
        project_id=project_id,
        entity_id=entity_id,
    )
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
    preparer: AcceptedNoteMutationPreparer,
    data: EntitySchema,
    *,
    check_storage_exists: bool,
    session: AsyncSession,
) -> AcceptedPreparedNoteWrite:
    """Prepare a new accepted note or raise a typed mutation rejection."""
    try:
        conflicting_note_paths = [
            path
            for path in await preparer.detect_file_path_conflicts(
                data.file_path,
                session=session,
            )
            if runtime_file_path_is_markdown_note(path)
        ]
        if conflicting_note_paths:
            joined_paths = ", ".join(sorted(conflicting_note_paths))
            reject_accepted_note_mutation(
                AcceptedNoteMutationRejectKind.conflict,
                "A note with an equivalent filename already exists: "
                f"{joined_paths}. Address the existing note explicitly to modify it.",
            )

        return await prepare_accepted_note_create(
            preparer,
            data,
            check_storage_exists=check_storage_exists,
            # The explicit check above rejects only Markdown note conflicts.
            # EntityService's broader detector also reports similarly named
            # binary resources, which are valid alongside Markdown notes.
            skip_conflict_check=True,
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
