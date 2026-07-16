"""Tests for accepted-note mutation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteBaseChecksumConflict,
    AcceptedNoteCreateMutation,
    AcceptedNoteDeleteMutation,
    AcceptedNoteEditMutation,
    AcceptedNoteMutationActor,
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationMovePolicy,
    AcceptedNoteMutationRejectKind,
    AcceptedNoteMutationRejected,
    AcceptedNoteMoveMutation,
    AcceptedNoteUpdateMutation,
    run_accepted_note_create,
    run_accepted_note_delete,
    run_accepted_note_edit,
    run_accepted_note_move,
    run_accepted_note_update,
)
from basic_memory.indexing.accepted_note_search import AcceptedNoteSearchRow
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import AcceptedNoteContentWrite
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.runtime.note_content import RuntimeAcceptedNoteResponse
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.services.exceptions import EntityAlreadyExistsError


_NOW = datetime(2026, 6, 20, 14, 30, tzinfo=UTC)
_ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")


@dataclass(frozen=True, slots=True)
class _PreparedFields:
    title: str
    note_type: str
    entity_metadata: dict[str, object] | None
    content_type: str
    permalink: str | None
    file_path: str


@dataclass(frozen=True, slots=True)
class _PreparedWrite:
    markdown_content: str
    search_content: str
    entity_fields: _PreparedFields


@dataclass(frozen=True, slots=True)
class _PreparedMove:
    file_path: Path
    markdown_content: str
    search_content: str
    permalink: str | None


@pytest.fixture(autouse=True)
def _freeze_mutation_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the accepted-note mutation wall clock so mutations stamp a fixed instant."""
    monkeypatch.setattr(
        "basic_memory.indexing.accepted_note_mutation_runner.accepted_note_mutation_utc_now",
        lambda: _NOW,
    )


class _MutationSession:
    def __init__(self) -> None:
        self.deleted: list[object] = []
        self.flush_count = 0

    async def delete(self, value: object) -> None:
        self.deleted.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


class _CreatePreparer:
    def __init__(
        self,
        prepared: _PreparedWrite,
        *,
        prepared_move: _PreparedMove | None = None,
        move_destination_error: EntityAlreadyExistsError | None = None,
    ) -> None:
        self.prepared = prepared
        self.move_destination_error = move_destination_error
        self.prepared_move = prepared_move or _PreparedMove(
            file_path=Path(prepared.entity_fields.file_path),
            markdown_content=prepared.markdown_content,
            search_content=prepared.search_content,
            permalink=prepared.entity_fields.permalink,
        )
        self.calls: list[tuple[EntitySchema, bool, AsyncSession | None]] = []
        self.replace_calls: list[tuple[Entity, EntitySchema, str, AsyncSession | None]] = []
        self.edit_calls: list[
            tuple[Entity, str, str, str, str | None, str | None, int, bool, AsyncSession | None]
        ] = []
        self.move_calls: list[tuple[Entity, str, str, AsyncSession | None]] = []

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        session: AsyncSession | None = None,
    ) -> _PreparedWrite:
        self.calls.append((schema, check_storage_exists, session))
        return self.prepared

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        session: AsyncSession | None = None,
    ) -> _PreparedWrite:
        self.replace_calls.append((entity, schema, existing_content, session))
        return self.prepared

    async def prepare_edit_entity_content(
        self,
        entity: Entity,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: str | None = None,
        find_text: str | None = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
        session: AsyncSession | None = None,
    ) -> _PreparedWrite:
        self.edit_calls.append(
            (
                entity,
                current_content,
                operation,
                content,
                section,
                find_text,
                expected_replacements,
                replace_subsections,
                session,
            )
        )
        return self.prepared

    async def prepare_move_entity_content(
        self,
        entity: Entity,
        current_content: str,
        destination_path: str,
        *,
        session: AsyncSession | None = None,
    ) -> _PreparedMove:
        self.move_calls.append((entity, current_content, destination_path, session))
        return self.prepared_move

    async def verify_move_destination_absent(
        self,
        *,
        source_file_path: str,
        destination_file_path: str,
    ) -> None:
        if self.move_destination_error is not None:
            raise self.move_destination_error
        return None


class _PreparerFactory:
    def __init__(self, preparer: _CreatePreparer) -> None:
        self.preparer = preparer
        self.projects: list[Project] = []

    def create_note_preparer(self, project: Project) -> _CreatePreparer:
        self.projects.append(project)
        return self.preparer


class _ProjectRepository:
    def __init__(self, project: Project | None) -> None:
        self.project = project
        self.calls: list[tuple[AsyncSession, str]] = []

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
    ) -> Project | None:
        self.calls.append((session, external_id))
        return self.project


class _EntityLookupRepository:
    def __init__(
        self,
        *,
        by_external_id: Entity | None = None,
        by_file_path: Entity | None = None,
    ) -> None:
        self.by_external_id = by_external_id
        self.by_file_path = by_file_path
        self.external_id_calls: list[tuple[AsyncSession, str, bool]] = []
        self.file_path_calls: list[tuple[AsyncSession, str, bool]] = []

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
        *,
        load_relations: bool = False,
    ) -> Entity | None:
        self.external_id_calls.append((session, external_id, load_relations))
        return self.by_external_id

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: str,
        *,
        load_relations: bool = False,
    ) -> Entity | None:
        self.file_path_calls.append((session, file_path, load_relations))
        return self.by_file_path


class _NoteContentLookupRepository:
    def __init__(self, note_content: NoteContent | None = None) -> None:
        self.note_content = note_content
        self.calls: list[tuple[AsyncSession, int]] = []

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> NoteContent | None:
        self.calls.append((session, entity_id))
        return self.note_content


class _PendingEntityRepository:
    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self.calls: list[tuple[AsyncSession, AcceptedPendingEntityWrite]] = []

    async def create_pending_accepted_entity(
        self,
        session: AsyncSession,
        write: AcceptedPendingEntityWrite,
    ) -> Entity:
        self.calls.append((session, write))
        self.entity.title = write.title
        self.entity.note_type = write.note_type
        self.entity.entity_metadata = write.entity_metadata
        self.entity.content_type = write.content_type
        self.entity.permalink = write.permalink
        self.entity.file_path = write.file_path
        self.entity.created_at = write.created_at
        self.entity.updated_at = write.updated_at
        self.entity.created_by = write.created_by
        self.entity.last_updated_by = write.last_updated_by
        return self.entity


class _NoteContentAcceptRepository:
    def __init__(self, note_content: NoteContent) -> None:
        self.note_content = note_content
        self.calls: list[tuple[AsyncSession, AcceptedNoteContentWrite]] = []

    async def accept_write(
        self,
        session: AsyncSession,
        write: AcceptedNoteContentWrite,
    ) -> NoteContent:
        self.calls.append((session, write))
        self.note_content.markdown_content = write.markdown_content
        self.note_content.db_version = write.db_version
        self.note_content.db_checksum = write.db_checksum
        self.note_content.last_source = write.last_source
        self.note_content.updated_at = write.updated_at
        return self.note_content


class _SearchRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[AsyncSession, AcceptedNoteSearchRow]] = []
        self.deleted_entity_ids: list[int] = []
        self.deleted_vector_entity_ids: list[int] = []

    async def refresh_entity(
        self,
        session: AsyncSession,
        row: AcceptedNoteSearchRow,
    ) -> None:
        self.calls.append((session, row))

    async def delete_entity(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        _ = session
        self.deleted_entity_ids.append(entity_id)

    async def delete_entity_vectors(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        _ = session
        self.deleted_vector_entity_ids.append(entity_id)


@dataclass(frozen=True, slots=True)
class _MutationLookupRepositories:
    entity_lookup_repository: _EntityLookupRepository
    note_content_lookup_repository: _NoteContentLookupRepository

    def entity_repository(self, project_id: int) -> _EntityLookupRepository:
        _ = project_id
        return self.entity_lookup_repository

    def note_content_repository(self, project_id: int) -> _NoteContentLookupRepository:
        _ = project_id
        return self.note_content_lookup_repository


@dataclass(frozen=True, slots=True)
class _MutationWriteRepositories:
    pending_entity_repository_result: _PendingEntityRepository
    note_content_accept_repository_result: _NoteContentAcceptRepository
    search_repository_result: _SearchRepository

    def pending_entity_repository(self, project_id: int) -> _PendingEntityRepository:
        _ = project_id
        return self.pending_entity_repository_result

    def note_content_repository(self, project_id: int) -> _NoteContentAcceptRepository:
        _ = project_id
        return self.note_content_accept_repository_result

    def search_repository(self, project_id: int) -> _SearchRepository:
        _ = project_id
        return self.search_repository_result


def _project() -> Project:
    return cast(
        Project,
        SimpleNamespace(id=7, external_id="project-123", path="/tmp/basic-memory"),
    )


def _schema() -> EntitySchema:
    return EntitySchema(
        title="Accepted",
        directory="notes",
        note_type="note",
        content_type="text/markdown",
        content="# Accepted\n",
    )


def _prepared() -> _PreparedWrite:
    return _PreparedWrite(
        markdown_content="# Accepted\n",
        search_content="Accepted",
        entity_fields=_PreparedFields(
            title="Accepted",
            note_type="note",
            entity_metadata={"status": "draft"},
            content_type="text/markdown",
            permalink="accepted",
            file_path="notes/accepted.md",
        ),
    )


def _prepared_replacement() -> _PreparedWrite:
    return _PreparedWrite(
        markdown_content="# Replacement\n",
        search_content="Replacement",
        entity_fields=_PreparedFields(
            title="Replacement",
            note_type="note",
            entity_metadata={"status": "updated"},
            content_type="text/markdown",
            permalink="replacement",
            file_path="notes/replacement.md",
        ),
    )


def _prepared_move() -> _PreparedMove:
    return _PreparedMove(
        file_path=Path("archive/accepted.md"),
        markdown_content="# Moved\n",
        search_content="Moved",
        permalink="archive/accepted",
    )


def _entity(
    *,
    file_path: str = "notes/pending.md",
    permalink: str | None = "accepted",
    content_type: str = "text/markdown",
) -> Entity:
    return Entity(
        id=42,
        external_id="note-123",
        project_id=7,
        title="Pending",
        note_type="note",
        entity_metadata=None,
        content_type=content_type,
        permalink=permalink,
        file_path=file_path,
        checksum=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _note_content(entity: Entity) -> NoteContent:
    return NoteContent(
        entity_id=entity.id,
        project_id=entity.project_id,
        external_id=entity.external_id,
        file_path=Path(entity.file_path).as_posix(),
        markdown_content="# Old\n",
        db_version=1,
        db_checksum="old-checksum",
        file_version=1,
        file_checksum="file-checksum",
        file_write_status="pending",
        last_source=None,
    )


def _dependencies(
    *,
    project_repository: _ProjectRepository,
    entity_lookup_repository: _EntityLookupRepository,
    note_content_lookup_repository: _NoteContentLookupRepository,
    preparer_factory: _PreparerFactory,
    pending_entity_repository: _PendingEntityRepository,
    note_content_accept_repository: _NoteContentAcceptRepository,
    search_repository: _SearchRepository,
    move_policy: AcceptedNoteMutationMovePolicy | None = None,
    verify_storage_absent_on_create: bool = False,
) -> AcceptedNoteMutationDependencies:
    return AcceptedNoteMutationDependencies(
        project_repository=project_repository,
        lookup_repositories=_MutationLookupRepositories(
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
        ),
        preparer_factory=preparer_factory,
        write_repositories=_MutationWriteRepositories(
            pending_entity_repository_result=pending_entity_repository,
            note_content_accept_repository_result=note_content_accept_repository,
            search_repository_result=search_repository,
        ),
        move_policy=move_policy
        or AcceptedNoteMutationMovePolicy(
            disable_permalinks=False,
            update_permalinks_on_move=False,
        ),
        verify_storage_absent_on_create=verify_storage_absent_on_create,
    )


@pytest.mark.asyncio
async def test_run_accepted_note_create_persists_prepared_markdown() -> None:
    session = cast(AsyncSession, object())
    schema = _schema()
    project = _project()
    prepared = _prepared()
    entity = _entity()
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository()
    note_content_lookup_repository = _NoteContentLookupRepository()
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_create(
        session,
        request=AcceptedNoteCreateMutation(
            project_external_id="project-123",
            data=schema,
            actor=AcceptedNoteMutationActor(
                user_profile_id=_ACTOR_ID,
                kind="user",
                name="Ada",
            ),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
        ),
    )

    assert project_repository.calls == [(session, "project-123")]
    assert entity_lookup_repository.file_path_calls == [(session, "notes/Accepted.md", False)]
    assert preparer_factory.projects == [project]
    assert preparer.calls == [(schema, False, session)]
    assert pending_entity_repository.calls[0][1].created_by == str(_ACTOR_ID)
    assert note_content_accept_repository.calls[0][1].markdown_content == "# Accepted\n"
    assert note_content_accept_repository.calls[0][1].db_version == 1
    assert search_repository.calls[0][1].content_snippet == "Accepted"
    assert change.status_code == 201
    assert isinstance(change.payload, RuntimeAcceptedNoteResponse)
    payload = change.payload
    assert payload.external_id == "note-123"
    assert payload.markdown_content == "# Accepted\n"
    assert change.materialization is not None
    assert change.materialization.actor_user_profile_id == _ACTOR_ID
    assert change.materialization.actor_kind == "user"
    assert change.materialization.actor_name == "Ada"
    assert change.materialization.previous_file_path is None


@pytest.mark.asyncio
async def test_run_accepted_note_update_replaces_existing_note_content() -> None:
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_update(
        cast(AsyncSession, session),
        request=AcceptedNoteUpdateMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            data=schema,
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
        ),
    )

    assert entity_lookup_repository.external_id_calls == [
        (cast(AsyncSession, session), "note-123", False)
    ]
    assert note_content_lookup_repository.calls == [(cast(AsyncSession, session), entity.id)]
    assert preparer.replace_calls == [(entity, schema, "# Old\n", cast(AsyncSession, session))]
    assert session.flush_count == 1
    assert note_content_accept_repository.calls[0][1].db_version == 2
    assert note_content_accept_repository.calls[0][1].markdown_content == "# Replacement\n"
    assert change.status_code == 200
    assert isinstance(change.payload, RuntimeAcceptedNoteResponse)
    assert change.payload.title == "Replacement"
    assert change.materialization is not None
    assert change.materialization.db_version == 2
    assert change.materialization.previous_file_path is None


@pytest.mark.asyncio
async def test_run_accepted_note_update_accepts_matching_base_checksum() -> None:
    # The caller's synced base ("old-checksum" in the fixture) still matches the
    # accepted row, so the precondition holds and the replace lands unchanged.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_update(
        cast(AsyncSession, session),
        request=AcceptedNoteUpdateMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            data=schema,
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
            base_checksum="old-checksum",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
        ),
    )

    assert change.status_code == 200
    assert note_content_accept_repository.calls[0][1].db_version == 2
    assert note_content_accept_repository.calls[0][1].markdown_content == "# Replacement\n"


@pytest.mark.asyncio
async def test_run_accepted_note_update_rejects_stale_base_checksum() -> None:
    # The accepted row advanced past the caller's synced base: reject with the
    # current checksum in the structured detail so the client rebases instead of
    # clobbering the newer write (issue #1445).
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    with pytest.raises(AcceptedNoteMutationRejected) as exc_info:
        await run_accepted_note_update(
            cast(AsyncSession, session),
            request=AcceptedNoteUpdateMutation(
                project_external_id="project-123",
                entity_external_id="note-123",
                data=schema,
                actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
                source="api",
                base_checksum="stale-checksum",
            ),
            dependencies=_dependencies(
                project_repository=project_repository,
                entity_lookup_repository=entity_lookup_repository,
                note_content_lookup_repository=note_content_lookup_repository,
                preparer_factory=preparer_factory,
                pending_entity_repository=pending_entity_repository,
                note_content_accept_repository=note_content_accept_repository,
                search_repository=search_repository,
            ),
        )

    rejection = exc_info.value.rejection
    assert rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert rejection.kind.http_status_code == 409
    assert isinstance(rejection.detail, AcceptedNoteBaseChecksumConflict)
    assert rejection.detail.db_checksum == "old-checksum"
    assert rejection.detail.as_json_dict() == {
        "message": "Note changed since your last sync",
        "db_checksum": "old-checksum",
    }
    # Rejected before any replacement prepare or persistence ran.
    assert preparer.replace_calls == []
    assert note_content_accept_repository.calls == []
    assert search_repository.calls == []


@pytest.mark.asyncio
async def test_run_accepted_note_update_rejects_base_checksum_when_entity_missing() -> None:
    # A base_checksum with no addressed entity means the note was deleted after
    # the caller's pre-read; creating it here would silently resurrect the
    # just-deleted note, so reject with db_checksum None (nothing to rebase
    # against, issue #1445).
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared()
    entity = _entity()
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository()
    note_content_lookup_repository = _NoteContentLookupRepository()
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    with pytest.raises(AcceptedNoteMutationRejected) as exc_info:
        await run_accepted_note_update(
            cast(AsyncSession, session),
            request=AcceptedNoteUpdateMutation(
                project_external_id="project-123",
                entity_external_id="note-123",
                data=schema,
                actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
                source="api",
                base_checksum="old-checksum",
            ),
            dependencies=_dependencies(
                project_repository=project_repository,
                entity_lookup_repository=entity_lookup_repository,
                note_content_lookup_repository=note_content_lookup_repository,
                preparer_factory=preparer_factory,
                pending_entity_repository=pending_entity_repository,
                note_content_accept_repository=note_content_accept_repository,
                search_repository=search_repository,
            ),
        )

    rejection = exc_info.value.rejection
    assert rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert isinstance(rejection.detail, AcceptedNoteBaseChecksumConflict)
    assert rejection.detail.db_checksum is None
    assert rejection.detail.as_json_dict() == {
        "message": "Note changed since your last sync",
        "db_checksum": None,
    }
    # No entity was resurrected and nothing persisted.
    assert preparer.calls == []
    assert pending_entity_repository.calls == []
    assert note_content_accept_repository.calls == []


@pytest.mark.asyncio
async def test_run_accepted_note_update_creates_missing_entity_without_base_checksum() -> None:
    # Without a precondition the PUT keeps its upsert contract: a missing
    # addressed entity is created (201) exactly as before.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared()
    entity = _entity()
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository()
    note_content_lookup_repository = _NoteContentLookupRepository()
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_update(
        cast(AsyncSession, session),
        request=AcceptedNoteUpdateMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            data=schema,
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
        ),
    )

    assert change.status_code == 201
    assert len(pending_entity_repository.calls) == 1
    assert note_content_accept_repository.calls[0][1].db_version == 1


@pytest.mark.asyncio
async def test_run_accepted_note_update_rejects_rename_onto_unindexed_storage() -> None:
    # A PUT that renames the entity onto a path occupied by an on-disk but unindexed
    # file must reject with 409, mirroring the create/move storage guard, rather than
    # silently overwriting/losing that write.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/original.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(
        prepared,
        move_destination_error=EntityAlreadyExistsError(
            "file already exists at destination path: notes/Accepted.md"
        ),
    )
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    with pytest.raises(AcceptedNoteMutationRejected) as exc_info:
        await run_accepted_note_update(
            cast(AsyncSession, session),
            request=AcceptedNoteUpdateMutation(
                project_external_id="project-123",
                entity_external_id="note-123",
                data=schema,
                actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
                source="api",
            ),
            dependencies=_dependencies(
                project_repository=project_repository,
                entity_lookup_repository=entity_lookup_repository,
                note_content_lookup_repository=note_content_lookup_repository,
                preparer_factory=preparer_factory,
                pending_entity_repository=pending_entity_repository,
                note_content_accept_repository=note_content_accept_repository,
                search_repository=search_repository,
                verify_storage_absent_on_create=True,
            ),
        )

    assert exc_info.value.rejection.kind is AcceptedNoteMutationRejectKind.conflict
    # The write was rejected before any note_content/search persistence ran.
    assert note_content_accept_repository.calls == []
    assert search_repository.calls == []


@pytest.mark.asyncio
async def test_run_accepted_note_update_rejects_non_markdown_existing_entity() -> None:
    # PUTting markdown at a watcher-indexed binary entity has no markdown note_content
    # to replace; the runner must return 415 (unsupported media type), not a
    # permanent-looking 409 content-backfill retry.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/image.png", content_type="image/png")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    with pytest.raises(AcceptedNoteMutationRejected) as exc_info:
        await run_accepted_note_update(
            cast(AsyncSession, session),
            request=AcceptedNoteUpdateMutation(
                project_external_id="project-123",
                entity_external_id="note-123",
                data=schema,
                actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
                source="api",
            ),
            dependencies=_dependencies(
                project_repository=project_repository,
                entity_lookup_repository=entity_lookup_repository,
                note_content_lookup_repository=note_content_lookup_repository,
                preparer_factory=preparer_factory,
                pending_entity_repository=pending_entity_repository,
                note_content_accept_repository=note_content_accept_repository,
                search_repository=search_repository,
            ),
        )

    assert exc_info.value.rejection.kind is AcceptedNoteMutationRejectKind.unsupported_media_type
    assert exc_info.value.rejection.kind.http_status_code == 415
    # Rejected before any note_content load or persistence.
    assert note_content_lookup_repository.calls == []
    assert note_content_accept_repository.calls == []


@pytest.mark.asyncio
async def test_run_accepted_note_edit_applies_patch_against_db_content() -> None:
    session = _MutationSession()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_edit(
        cast(AsyncSession, session),
        request=AcceptedNoteEditMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            data=EditEntityRequest(
                operation="find_replace",
                content="# Replacement",
                find_text="# Old",
                expected_replacements=1,
            ),
            actor=AcceptedNoteMutationActor(user_profile_id=None),
            source="mcp",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
        ),
    )

    assert preparer.edit_calls == [
        (
            entity,
            "# Old\n",
            "find_replace",
            "# Replacement",
            None,
            "# Old",
            1,
            True,
            cast(AsyncSession, session),
        )
    ]
    assert note_content_accept_repository.calls[0][1].last_source == "mcp"
    assert change.status_code == 200
    assert change.materialization is not None
    assert change.materialization.source == "mcp"


@pytest.mark.asyncio
@pytest.mark.parametrize("file_checksum", ["file-checksum", None])
async def test_run_accepted_note_move_carries_previous_path_and_materialized_cleanup(
    file_checksum: str | None,
) -> None:
    session = _MutationSession()
    project = _project()
    prepared = _prepared_replacement()
    prepared_move = _prepared_move()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    note_content.file_checksum = file_checksum
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared, prepared_move=prepared_move)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_move(
        cast(AsyncSession, session),
        request=AcceptedNoteMoveMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            destination_path="archive/accepted.md",
            actor=AcceptedNoteMutationActor(
                user_profile_id=_ACTOR_ID,
                kind="mcp",
                name="Claude",
            ),
            source="mcp",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
            move_policy=AcceptedNoteMutationMovePolicy(
                disable_permalinks=False,
                update_permalinks_on_move=True,
            ),
        ),
    )

    assert preparer.move_calls == [
        (entity, "# Old\n", "archive/accepted.md", cast(AsyncSession, session))
    ]
    assert entity.file_path == "archive/accepted.md"
    assert entity.permalink == "archive/accepted"
    assert change.status_code == 200
    assert change.materialization is not None
    assert change.materialization.previous_file_path == "notes/accepted.md"
    cleanup = change.materialization.cleanup_after_write
    if file_checksum is None:
        assert cleanup is None
    else:
        assert cleanup is not None
        assert cleanup.file_path == "notes/accepted.md"
        assert cleanup.file_checksum == "file-checksum"


@pytest.mark.asyncio
async def test_run_accepted_note_move_rejects_same_file_path() -> None:
    session = _MutationSession()
    project = _project()
    prepared = _prepared()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(prepared)
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    with pytest.raises(AcceptedNoteMutationRejected) as exc_info:
        await run_accepted_note_move(
            cast(AsyncSession, session),
            request=AcceptedNoteMoveMutation(
                project_external_id="project-123",
                entity_external_id="note-123",
                destination_path="notes/accepted.md",
                actor=AcceptedNoteMutationActor(user_profile_id=None),
                source="mcp",
            ),
            dependencies=_dependencies(
                project_repository=project_repository,
                entity_lookup_repository=entity_lookup_repository,
                note_content_lookup_repository=note_content_lookup_repository,
                preparer_factory=preparer_factory,
                pending_entity_repository=pending_entity_repository,
                note_content_accept_repository=note_content_accept_repository,
                search_repository=search_repository,
            ),
        )

    assert exc_info.value.rejection.kind is AcceptedNoteMutationRejectKind.bad_request
    assert exc_info.value.rejection.detail == "Source and destination paths are the same."


@pytest.mark.asyncio
async def test_run_accepted_note_delete_removes_entity_and_returns_cleanup() -> None:
    session = _MutationSession()
    project = _project()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    project_repository = _ProjectRepository(project)
    entity_lookup_repository = _EntityLookupRepository(by_external_id=entity)
    note_content_lookup_repository = _NoteContentLookupRepository(note_content)
    preparer = _CreatePreparer(_prepared())
    preparer_factory = _PreparerFactory(preparer)
    pending_entity_repository = _PendingEntityRepository(entity)
    note_content_accept_repository = _NoteContentAcceptRepository(note_content)
    search_repository = _SearchRepository()

    change = await run_accepted_note_delete(
        cast(AsyncSession, session),
        request=AcceptedNoteDeleteMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
        ),
        dependencies=_dependencies(
            project_repository=project_repository,
            entity_lookup_repository=entity_lookup_repository,
            note_content_lookup_repository=note_content_lookup_repository,
            preparer_factory=preparer_factory,
            pending_entity_repository=pending_entity_repository,
            note_content_accept_repository=note_content_accept_repository,
            search_repository=search_repository,
        ),
    )

    assert session.deleted == [entity]
    assert search_repository.deleted_entity_ids == [entity.id]
    assert search_repository.deleted_vector_entity_ids == [entity.id]
    assert change.status_code == 200
    assert change.file_delete is not None
    assert change.file_delete.file_path == "notes/accepted.md"
    assert change.file_delete.file_checksum == "file-checksum"
