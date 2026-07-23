"""Tests for accepted-note mutation orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import basic_memory.indexing.accepted_note_mutation_runner as accepted_note_mutation_module
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
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation as MarkdownObservation,
    Relation as MarkdownRelation,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import (
    AcceptedNoteContentWrite,
    AcceptedObservationWrite,
    AcceptedRelationWrite,
)
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.runtime.note_content import RuntimeAcceptedNoteResponse
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.services.exceptions import EntityAlreadyExistsError
from basic_memory.services.note_preparation import (
    PreparedEntityFields,
    PreparedEntityMove,
    PreparedEntityWrite,
)


_NOW = datetime(2026, 6, 20, 14, 30, tzinfo=UTC)
_PREPARED_CREATED_AT = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
_PREPARED_UPDATED_AT = datetime(2024, 1, 16, 11, 45, tzinfo=UTC)
_ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")


def _prepared_write(
    *,
    markdown_content: str,
    search_content: str,
    entity_fields: PreparedEntityFields,
    observations: Sequence[AcceptedObservationWrite] = (),
    relations: Sequence[AcceptedRelationWrite] = (),
) -> PreparedEntityWrite:
    entity_markdown = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={
                "title": entity_fields.title,
                "type": entity_fields.note_type,
                "permalink": entity_fields.permalink,
            }
        ),
        content=markdown_content,
        observations=[
            MarkdownObservation(
                content=observation.content,
                category=observation.category,
                context=observation.context,
                tags=observation.tags,
            )
            for observation in observations
        ],
        relations=[
            MarkdownRelation(
                type=relation.relation_type,
                target=relation.target_name,
                context=relation.context,
            )
            for relation in relations
        ],
    )
    return PreparedEntityWrite(
        file_path=Path(entity_fields.file_path),
        markdown_content=markdown_content,
        search_content=search_content,
        entity_fields=entity_fields,
        entity_markdown=entity_markdown,
    )


@pytest.fixture(autouse=True)
def _freeze_mutation_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the accepted-note mutation wall clock so mutations stamp a fixed instant."""
    monkeypatch.setattr(
        "basic_memory.indexing.accepted_note_mutation_runner.accepted_note_mutation_utc_now",
        lambda: _NOW,
    )


@pytest.fixture
def persistence_calls(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    """Record which complete or move-only persistence boundary each mutation uses."""
    snapshot = AsyncMock(wraps=accepted_note_mutation_module.persist_accepted_note_snapshot)
    move = AsyncMock(wraps=accepted_note_mutation_module.persist_accepted_note_move)
    monkeypatch.setattr(accepted_note_mutation_module, "persist_accepted_note_snapshot", snapshot)
    monkeypatch.setattr(accepted_note_mutation_module, "persist_accepted_note_move", move)
    return snapshot, move


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
        prepared: PreparedEntityWrite,
        *,
        prepared_move: PreparedEntityMove | None = None,
        move_destination_error: EntityAlreadyExistsError | None = None,
        filename_conflicts: list[str] | None = None,
    ) -> None:
        self.prepared = prepared
        self.move_destination_error = move_destination_error
        self.filename_conflicts = filename_conflicts or []
        self.prepared_move = prepared_move or PreparedEntityMove(
            file_path=Path(prepared.entity_fields.file_path),
            markdown_content=prepared.markdown_content,
            search_content=prepared.search_content,
            permalink=prepared.entity_fields.permalink,
        )
        self.calls: list[tuple[EntitySchema, bool, AsyncSession | None]] = []
        self.skip_conflict_checks: list[bool] = []
        self.conflict_calls: list[tuple[str, bool, AsyncSession | None]] = []
        self.replace_calls: list[tuple[Entity, EntitySchema, str, AsyncSession | None]] = []
        self.edit_calls: list[
            tuple[
                Entity,
                str,
                str,
                str,
                str | None,
                str | None,
                int,
                bool,
                dict | None,
                AsyncSession | None,
            ]
        ] = []
        self.move_calls: list[tuple[Entity, str, str, AsyncSession | None]] = []
        self.self_relation_calls: list[tuple[str, Entity, AsyncSession | None]] = []

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        self.calls.append((schema, check_storage_exists, session))
        self.skip_conflict_checks.append(skip_conflict_check)
        return self.prepared

    async def detect_file_path_conflicts(
        self,
        file_path: str,
        skip_check: bool = False,
        session: AsyncSession | None = None,
    ) -> list[str]:
        self.conflict_calls.append((file_path, skip_check, session))
        return self.filename_conflicts

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
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
        metadata: dict | None = None,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
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
                metadata,
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
    ) -> PreparedEntityMove:
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

    async def resolve_deferred_self_relation(
        self,
        target: str,
        entity: Entity,
        session: AsyncSession | None = None,
    ) -> Entity | None:
        self.self_relation_calls.append((target, entity, session))
        candidates = {entity.file_path, entity.permalink}
        if entity.file_path.endswith(".md"):
            candidates.add(entity.file_path[:-3])
        return entity if target in candidates else None


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


class _ObservationRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Sequence[AcceptedObservationWrite]]] = []

    async def replace_accepted_observations(
        self,
        session: AsyncSession,
        entity_id: int,
        observations: Sequence[AcceptedObservationWrite],
    ) -> None:
        _ = session
        self.calls.append((entity_id, list(observations)))


class _RelationRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[int, Sequence[AcceptedRelationWrite]]] = []

    async def replace_accepted_outgoing_relations(
        self,
        session: AsyncSession,
        entity_id: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> None:
        _ = session
        self.calls.append((entity_id, list(relations)))


@dataclass(frozen=True, slots=True)
class _MutationWriteRepositories:
    pending_entity_repository_result: _PendingEntityRepository
    note_content_accept_repository_result: _NoteContentAcceptRepository
    search_repository_result: _SearchRepository
    observation_repository_result: _ObservationRepository
    relation_repository_result: _RelationRepository

    def pending_entity_repository(self, project_id: int) -> _PendingEntityRepository:
        _ = project_id
        return self.pending_entity_repository_result

    def note_content_repository(self, project_id: int) -> _NoteContentAcceptRepository:
        _ = project_id
        return self.note_content_accept_repository_result

    def search_repository(self, project_id: int) -> _SearchRepository:
        _ = project_id
        return self.search_repository_result

    def observation_repository(self, project_id: int) -> _ObservationRepository:
        _ = project_id
        return self.observation_repository_result

    def relation_repository(self, project_id: int) -> _RelationRepository:
        _ = project_id
        return self.relation_repository_result


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


def _prepared() -> PreparedEntityWrite:
    return _prepared_write(
        markdown_content="# Accepted\n",
        search_content="Accepted",
        entity_fields=PreparedEntityFields(
            title="Accepted",
            note_type="note",
            entity_metadata={"status": "draft"},
            content_type="text/markdown",
            permalink="accepted",
            file_path="notes/accepted.md",
            created_at=_PREPARED_CREATED_AT,
            updated_at=_PREPARED_UPDATED_AT,
        ),
    )


def _prepared_replacement() -> PreparedEntityWrite:
    return _prepared_write(
        markdown_content="# Replacement\n",
        search_content="Replacement",
        entity_fields=PreparedEntityFields(
            title="Replacement",
            note_type="note",
            entity_metadata={"status": "updated"},
            content_type="text/markdown",
            permalink="replacement",
            file_path="notes/replacement.md",
            created_at=_PREPARED_CREATED_AT,
            updated_at=_PREPARED_UPDATED_AT,
        ),
    )


def _prepared_move() -> PreparedEntityMove:
    return PreparedEntityMove(
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


def _note_content(
    entity: Entity,
    last_source: str | None = None,
    file_write_status: str = "pending",
) -> NoteContent:
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
        file_write_status=file_write_status,
        last_source=last_source,
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
    observation_repository: _ObservationRepository | None = None,
    relation_repository: _RelationRepository | None = None,
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
            observation_repository_result=observation_repository or _ObservationRepository(),
            relation_repository_result=relation_repository or _RelationRepository(),
        ),
        move_policy=move_policy
        or AcceptedNoteMutationMovePolicy(
            disable_permalinks=False,
            update_permalinks_on_move=False,
        ),
        verify_storage_absent_on_create=verify_storage_absent_on_create,
    )


@pytest.mark.asyncio
async def test_run_accepted_note_create_persists_prepared_markdown(
    persistence_calls: tuple[AsyncMock, AsyncMock],
) -> None:
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
    assert preparer.conflict_calls == [("notes/Accepted.md", False, session)]
    assert preparer.calls == [(schema, False, session)]
    assert preparer.skip_conflict_checks == [True]
    assert pending_entity_repository.calls[0][1].created_by == str(_ACTOR_ID)
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
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
    assert persistence_calls[0].await_count == 1
    assert persistence_calls[1].await_count == 0


@pytest.mark.asyncio
async def test_run_accepted_note_create_rejects_equivalent_markdown_file_path() -> None:
    session = cast(AsyncSession, object())
    schema = _schema()
    project = _project()
    preparer = _CreatePreparer(
        _prepared(),
        filename_conflicts=["notes/accepted.md"],
    )

    with pytest.raises(AcceptedNoteMutationRejected) as exc_info:
        await run_accepted_note_create(
            session,
            request=AcceptedNoteCreateMutation(
                project_external_id="project-123",
                data=schema,
                actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
                source="api",
            ),
            dependencies=_dependencies(
                project_repository=_ProjectRepository(project),
                entity_lookup_repository=_EntityLookupRepository(),
                note_content_lookup_repository=_NoteContentLookupRepository(),
                preparer_factory=_PreparerFactory(preparer),
                pending_entity_repository=_PendingEntityRepository(_entity()),
                note_content_accept_repository=_NoteContentAcceptRepository(
                    _note_content(_entity())
                ),
                search_repository=_SearchRepository(),
            ),
        )

    assert exc_info.value.rejection.kind is AcceptedNoteMutationRejectKind.conflict
    assert "notes/accepted.md" in str(exc_info.value.rejection.detail)
    assert preparer.conflict_calls == [("notes/Accepted.md", False, session)]
    assert preparer.calls == []


@pytest.mark.asyncio
async def test_run_accepted_note_create_allows_equivalent_non_markdown_resource_path() -> None:
    session = cast(AsyncSession, object())
    schema = _schema()
    project = _project()
    prepared = _prepared()
    entity = _entity()
    preparer = _CreatePreparer(
        prepared,
        filename_conflicts=["notes/accepted.png"],
    )

    change = await run_accepted_note_create(
        session,
        request=AcceptedNoteCreateMutation(
            project_external_id="project-123",
            data=schema,
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=_ProjectRepository(project),
            entity_lookup_repository=_EntityLookupRepository(),
            note_content_lookup_repository=_NoteContentLookupRepository(),
            preparer_factory=_PreparerFactory(preparer),
            pending_entity_repository=_PendingEntityRepository(entity),
            note_content_accept_repository=_NoteContentAcceptRepository(_note_content(entity)),
            search_repository=_SearchRepository(),
        ),
    )

    assert change.status_code == 201
    assert preparer.conflict_calls == [("notes/Accepted.md", False, session)]
    assert preparer.skip_conflict_checks == [True]


@pytest.mark.asyncio
async def test_run_accepted_note_update_replaces_existing_note_content(
    persistence_calls: tuple[AsyncMock, AsyncMock],
) -> None:
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
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
    assert change.status_code == 200
    assert isinstance(change.payload, RuntimeAcceptedNoteResponse)
    assert change.payload.title == "Replacement"
    assert change.materialization is not None
    assert change.materialization.db_version == 2
    assert change.materialization.previous_file_path is None
    assert persistence_calls[0].await_count == 1
    assert persistence_calls[1].await_count == 0


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
async def test_run_accepted_note_update_accepts_relay_self_supersede_on_stale_base() -> None:
    # Lost-ack wedge regression (#1589, 2026-07-23 production incident): a relay
    # persist timed out client-side AFTER committing, so the accepted row is the
    # relay's own write while the relay's recorded base is one version behind.
    # The relay superseding its own prior write is never a real conflict - the
    # live Y.Doc is the merge of everything the relay ever persisted - so the
    # stale base must be accepted, not 409-wedged forever.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity, last_source="collaboration_relay")
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
            source="collaboration_relay",
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

    assert change.status_code == 200
    assert note_content_accept_repository.calls[0][1].db_version == 2
    assert note_content_accept_repository.calls[0][1].markdown_content == "# Replacement\n"


@pytest.mark.asyncio
async def test_run_accepted_note_update_relay_supersedes_foreign_head() -> None:
    # Hot-doc canonical (#1589 Phase G): a relay persist is an unconditional
    # versioned export, superseding even a FOREIGN current head (MCP here).
    # The foreign version survives as file history and the reconciler surfaces
    # the conflict from the live-update event; nothing is destroyed.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    # The foreign head is materialized ('synced'): its object version exists,
    # so superseding it destroys nothing.
    note_content = _note_content(entity, last_source="mcp", file_write_status="synced")
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
            source="collaboration_relay",
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

    assert change.status_code == 200
    assert note_content_accept_repository.calls[0][1].db_version == 2


@pytest.mark.asyncio
async def test_run_accepted_note_update_relay_keeps_rejecting_unmaterialized_foreign_head() -> None:
    # A pending foreign head has NO storage object version yet: superseding it
    # would erase the only copy (its queued materialization preflights as
    # stale and never writes). The 409 holds until materialization lands; the
    # relay's next store retries seconds later (Codex review, PR #1146).
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity, last_source="mcp", file_write_status="pending")
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
                source="collaboration_relay",
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
    assert note_content_accept_repository.calls == []


@pytest.mark.asyncio
async def test_run_accepted_note_update_non_relay_stale_base_still_rejects() -> None:
    # The unconditional export is scoped to the relay writer only: any other
    # source with a stale base keeps the full guarded 409 semantics.
    session = _MutationSession()
    schema = _schema()
    project = _project()
    prepared = _prepared_replacement()
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity, last_source="collaboration_relay")
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
    assert note_content_accept_repository.calls == []


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
async def test_run_accepted_note_edit_applies_patch_against_db_content(
    persistence_calls: tuple[AsyncMock, AsyncMock],
) -> None:
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
            None,
            cast(AsyncSession, session),
        )
    ]
    assert note_content_accept_repository.calls[0][1].last_source == "mcp"
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
    assert change.status_code == 200
    assert change.materialization is not None
    assert change.materialization.source == "mcp"
    assert persistence_calls[0].await_count == 1
    assert persistence_calls[1].await_count == 0


@pytest.mark.asyncio
async def test_run_accepted_note_edit_threads_metadata_into_preparer() -> None:
    """The `metadata` field on EditEntityRequest must reach the edit preparer.

    Regression guard for issue #1011: `metadata` merges frontmatter fields
    independent of `operation`, so the accepted-note-edit runner must pass it
    through unchanged instead of silently dropping it.
    """
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

    await run_accepted_note_edit(
        cast(AsyncSession, session),
        request=AcceptedNoteEditMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            data=EditEntityRequest(
                operation="find_replace",
                content="# Replacement",
                find_text="# Old",
                expected_replacements=1,
                metadata={"status": "resolved"},
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
            {"status": "resolved"},
            cast(AsyncSession, session),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("file_checksum", ["file-checksum", None])
async def test_run_accepted_note_move_carries_previous_path_and_materialized_cleanup(
    file_checksum: str | None,
    persistence_calls: tuple[AsyncMock, AsyncMock],
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
    assert entity.created_at == _NOW
    assert entity.updated_at == _NOW
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
    assert persistence_calls[0].await_count == 0
    assert persistence_calls[1].await_count == 1


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


def _prepared_with_graph(
    *,
    observations: Sequence[AcceptedObservationWrite],
    relations: Sequence[AcceptedRelationWrite],
) -> PreparedEntityWrite:
    """A prepared accepted write carrying a parsed observation/relation graph."""
    return _prepared_write(
        markdown_content="# Accepted\n",
        search_content="Accepted",
        entity_fields=PreparedEntityFields(
            title="Accepted",
            note_type="dev_accept_person",
            entity_metadata={"type": "dev_accept_person"},
            content_type="text/markdown",
            permalink="accepted",
            file_path="notes/accepted.md",
            created_at=_PREPARED_CREATED_AT,
            updated_at=_PREPARED_UPDATED_AT,
        ),
        observations=observations,
        relations=relations,
    )


@pytest.mark.asyncio
async def test_run_accepted_note_create_persists_graph_rows() -> None:
    """Create persists observations/relations in the accept transaction (issue #1076).

    Regression for the DB-first write that returned 201 but left the observation
    and relation tables empty until a later index_file pass.
    """
    session = cast(AsyncSession, object())
    observations = [
        AcceptedObservationWrite(
            content="Ada Acceptance", category="name", context=None, tags=None
        ),
        AcceptedObservationWrite(content="Engineer", category="role", context=None, tags=None),
    ]
    relations = [
        AcceptedRelationWrite(relation_type="works_at", target_name="XSYS Target", context=None)
    ]
    prepared = _prepared_with_graph(observations=observations, relations=relations)
    entity = _entity()
    note_content = _note_content(entity)
    preparer_factory = _PreparerFactory(_CreatePreparer(prepared))
    observation_repository = _ObservationRepository()
    relation_repository = _RelationRepository()

    change = await run_accepted_note_create(
        session,
        request=AcceptedNoteCreateMutation(
            project_external_id="project-123",
            data=_schema(),
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=_ProjectRepository(_project()),
            entity_lookup_repository=_EntityLookupRepository(),
            note_content_lookup_repository=_NoteContentLookupRepository(),
            preparer_factory=preparer_factory,
            pending_entity_repository=_PendingEntityRepository(entity),
            note_content_accept_repository=_NoteContentAcceptRepository(note_content),
            search_repository=_SearchRepository(),
            observation_repository=observation_repository,
            relation_repository=relation_repository,
        ),
    )

    assert change.status_code == 201
    # The parsed graph is persisted against the new entity in the same transaction.
    assert observation_repository.calls == [(entity.id, observations)]
    assert relation_repository.calls == [(entity.id, relations)]


@pytest.mark.asyncio
async def test_run_accepted_note_create_resolves_self_relation_in_transaction() -> None:
    """Create resolves its own safe permalink before persisting the graph."""
    session = cast(AsyncSession, object())
    self_relation = AcceptedRelationWrite(
        relation_type="documents",
        target_name="accepted",
        context=None,
    )
    prepared = _prepared_with_graph(observations=[], relations=[self_relation])
    entity = _entity()
    note_content = _note_content(entity)
    preparer = _CreatePreparer(prepared)
    relation_repository = _RelationRepository()

    change = await run_accepted_note_create(
        session,
        request=AcceptedNoteCreateMutation(
            project_external_id="project-123",
            data=_schema(),
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=_ProjectRepository(_project()),
            entity_lookup_repository=_EntityLookupRepository(),
            note_content_lookup_repository=_NoteContentLookupRepository(),
            preparer_factory=_PreparerFactory(preparer),
            pending_entity_repository=_PendingEntityRepository(entity),
            note_content_accept_repository=_NoteContentAcceptRepository(note_content),
            search_repository=_SearchRepository(),
            relation_repository=relation_repository,
        ),
    )

    assert change.status_code == 201
    assert [call[0] for call in preparer.self_relation_calls] == ["accepted"]
    assert relation_repository.calls == [
        (
            entity.id,
            [
                AcceptedRelationWrite(
                    relation_type="documents",
                    target_name=entity.title,
                    context=None,
                    target_id=entity.id,
                )
            ],
        )
    ]


@pytest.mark.asyncio
async def test_run_accepted_note_update_replaces_graph_rows() -> None:
    """A PUT replace rewrites the note's full observation/relation set (issue #1076)."""
    session = _MutationSession()
    observations = [
        AcceptedObservationWrite(content="Replaced", category="note", context=None, tags=None)
    ]
    relations = [
        AcceptedRelationWrite(relation_type="relates_to", target_name="Other", context=None)
    ]
    prepared = _prepared_with_graph(observations=observations, relations=relations)
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    observation_repository = _ObservationRepository()
    relation_repository = _RelationRepository()

    change = await run_accepted_note_update(
        cast(AsyncSession, session),
        request=AcceptedNoteUpdateMutation(
            project_external_id="project-123",
            entity_external_id="note-123",
            data=_schema(),
            actor=AcceptedNoteMutationActor(user_profile_id=_ACTOR_ID),
            source="api",
        ),
        dependencies=_dependencies(
            project_repository=_ProjectRepository(_project()),
            entity_lookup_repository=_EntityLookupRepository(by_external_id=entity),
            note_content_lookup_repository=_NoteContentLookupRepository(note_content),
            preparer_factory=_PreparerFactory(_CreatePreparer(prepared)),
            pending_entity_repository=_PendingEntityRepository(entity),
            note_content_accept_repository=_NoteContentAcceptRepository(note_content),
            search_repository=_SearchRepository(),
            observation_repository=observation_repository,
            relation_repository=relation_repository,
        ),
    )

    assert change.status_code == 200
    assert observation_repository.calls == [(entity.id, observations)]
    assert relation_repository.calls == [(entity.id, relations)]


@pytest.mark.asyncio
async def test_run_accepted_note_edit_clears_graph_when_markdown_drops_it() -> None:
    """An edit that removes all observations/relations clears the graph rows (issue #1076)."""
    session = _MutationSession()
    prepared = _prepared_with_graph(observations=[], relations=[])
    entity = _entity(file_path="notes/accepted.md")
    note_content = _note_content(entity)
    observation_repository = _ObservationRepository()
    relation_repository = _RelationRepository()

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
            project_repository=_ProjectRepository(_project()),
            entity_lookup_repository=_EntityLookupRepository(by_external_id=entity),
            note_content_lookup_repository=_NoteContentLookupRepository(note_content),
            preparer_factory=_PreparerFactory(_CreatePreparer(prepared)),
            pending_entity_repository=_PendingEntityRepository(entity),
            note_content_accept_repository=_NoteContentAcceptRepository(note_content),
            search_repository=_SearchRepository(),
            observation_repository=observation_repository,
            relation_repository=relation_repository,
        ),
    )

    assert change.status_code == 200
    # An empty parsed set still hits the repos so stale rows are cleared, not left behind.
    assert observation_repository.calls == [(entity.id, [])]
    assert relation_repository.calls == [(entity.id, [])]
