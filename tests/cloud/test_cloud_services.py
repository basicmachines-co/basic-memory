from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from basic_memory.indexing import (
    AcceptedNoteCreateMutation,
    AcceptedNoteDeleteMutation,
    AcceptedNoteEditMutation,
    AcceptedNoteMoveMutation,
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationRejected,
    AcceptedNoteMutationRejection,
    AcceptedNoteMutationRejectKind,
    AcceptedNoteUpdateMutation,
    DirectoryDeleteRuntime,
    DirectoryDeleteRejectKind,
    NoteContentReadRepairFile,
    NoteContentReadRepairPreflight,
    NoteContentReadRepairTarget,
    NoteContentReadView,
)
from basic_memory.runtime import (
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteContentReadRepairStatus,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.cloud.note_content_reads as note_content_reads
import basic_memory.cloud.note_content_writes as note_content_writes
from basic_memory.cloud.directory_deletes import (
    DirectoryDeleteService,
    DirectoryDeleteServiceError,
)
from basic_memory.cloud.note_content_reads import NoteContentQueryService
from basic_memory.cloud.note_content_writes import (
    NoteContentMutationActorContext,
    NoteContentMutationService,
    NoteContentMutationServiceError,
)


def test_cloud_package_is_the_canonical_shared_cloud_surface() -> None:
    cloud = import_module("basic_memory.cloud")

    assert cloud.NoteContentQueryService is NoteContentQueryService
    assert cloud.NoteContentMutationService is NoteContentMutationService
    assert cloud.DirectoryDeleteService is DirectoryDeleteService


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def begin(self) -> FakeSession:
        return self


class FakeSessionMaker:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


class FakeDirectoryDeleteStore:
    async def load_project_id(
        self,
        session: AsyncSession,
        project_external_id: str,
    ) -> int | None:
        assert session is not None
        assert project_external_id == "project-123"
        return 3

    async def load_directory_file_snapshots(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        directory: str,
    ):
        assert session is not None
        assert project_id == 3
        assert directory == "notes"
        from basic_memory.runtime import RuntimeDirectoryFileSnapshot

        return [
            RuntimeDirectoryFileSnapshot(
                entity_id=7,
                file_path="notes/example.md",
                file_checksum="note-sha",
                last_modified_at=None,
                size=None,
            )
        ]

    async def delete_directory_entities(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        entity_ids,
    ) -> None:
        assert session is not None
        assert project_id == 3
        assert list(entity_ids) == [7]


class FakeDirectoryFileDeleteEnqueuer:
    def __init__(self) -> None:
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        self.requests.append(request)


class FakeReadRepairFileReader:
    def __init__(self, markdown_content: str) -> None:
        self.markdown_content = markdown_content
        self.targets: list[NoteContentReadRepairTarget] = []

    async def read_note_content_repair_file(
        self,
        target: NoteContentReadRepairTarget,
    ) -> NoteContentReadRepairFile:
        self.targets.append(target)
        return NoteContentReadRepairFile(
            markdown_content=self.markdown_content,
            observed_at=datetime(2026, 4, 13, 13, 0, tzinfo=UTC),
        )


def _entity(**overrides: object) -> SimpleNamespace:
    now = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "external_id": "note-456",
        "id": 42,
        "title": "Read note",
        "note_type": "note",
        "content_type": "text/markdown",
        "permalink": "main/notes/read-note",
        "file_path": "notes/read-note.md",
        "content": None,
        "entity_metadata": {},
        "observations": [],
        "relations": [],
        "created_at": now,
        "updated_at": now,
        "created_by": "creator",
        "last_updated_by": "editor",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _note_content(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "markdown_content": "# Read note\n",
        "db_version": 4,
        "db_checksum": "db-checksum",
        "file_version": 3,
        "file_checksum": "file-checksum",
        "file_write_status": "synced",
        "last_source": "api",
        "last_materialization_error": None,
        "file_updated_at": datetime(2026, 4, 13, 13, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session_maker() -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], object())


@pytest.mark.asyncio
async def test_note_content_query_service_loads_typed_read_payload(monkeypatch) -> None:
    tenant_session_maker = _session_maker()
    session = object()

    @asynccontextmanager
    async def fake_scoped_session(received_session_maker):
        assert received_session_maker is tenant_session_maker
        yield session

    async def fake_load_note_view(
        received_session,
        *,
        project_external_id: str,
        entity_external_id: str,
    ):
        assert received_session is session
        assert project_external_id == "project-123"
        assert entity_external_id == "note-456"
        return NoteContentReadView(
            entity=_entity(),
            note_content=_note_content(),
        )

    monkeypatch.setattr(note_content_reads.db, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(
        note_content_reads,
        "load_note_content_read_view_with_default_repositories",
        fake_load_note_view,
    )

    service = NoteContentQueryService(session_maker=tenant_session_maker)

    payload = await service.get_note_entity_payload(
        project_external_id="project-123",
        entity_external_id="note-456",
    )

    assert payload is not None
    response = cast(Any, payload)
    assert response.external_id == "note-456"
    assert response.markdown_content == "# Read note\n"
    assert response.db_version == 4
    assert response.file_write_status == "synced"


@pytest.mark.asyncio
async def test_note_content_read_repair_requires_real_file_reader(monkeypatch) -> None:
    tenant_session_maker = _session_maker()
    session = object()

    @asynccontextmanager
    async def fake_scoped_session(received_session_maker):
        assert received_session_maker is tenant_session_maker
        yield session

    async def fake_prepare_note_content_read_repair(
        received_session,
        *,
        project_external_id: str,
        entity_external_id: str,
    ):
        assert received_session is session
        assert project_external_id == "project-123"
        assert entity_external_id == "note-456"
        return NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=NoteContentReadRepairTarget(
                project=SimpleNamespace(id=7, path="/tmp/main"),
                entity=_entity(),
            ),
        )

    monkeypatch.setattr(note_content_reads.db, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(
        note_content_reads,
        "prepare_note_content_read_repair_with_default_repositories",
        fake_prepare_note_content_read_repair,
    )

    service = NoteContentQueryService(session_maker=tenant_session_maker)

    with pytest.raises(RuntimeError, match="requires a file reader"):
        await service.reconcile_note_content_from_file(
            project_external_id="project-123",
            entity_external_id="note-456",
            source="api",
        )


@pytest.mark.asyncio
async def test_note_content_read_repair_uses_reader_behavior(monkeypatch) -> None:
    tenant_session_maker = _session_maker()
    session = object()
    target = NoteContentReadRepairTarget(
        project=SimpleNamespace(id=7, path="/tmp/main"),
        entity=_entity(),
    )
    reader = FakeReadRepairFileReader("# Repaired\n")
    reconciled: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_scoped_session(received_session_maker):
        assert received_session_maker is tenant_session_maker
        yield session

    async def fake_prepare_note_content_read_repair(
        received_session,
        *,
        project_external_id: str,
        entity_external_id: str,
    ):
        assert received_session is session
        assert project_external_id == "project-123"
        assert entity_external_id == "note-456"
        return NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=target,
        )

    async def fake_run_note_content_read_repair(
        repair_preflight,
        *,
        session_maker,
        file_reader,
        source: str,
    ):
        assert repair_preflight.require_target() is target
        assert session_maker is tenant_session_maker
        repair_file = await file_reader.read_note_content_repair_file(target)
        reconciled.append(
            {
                "markdown_content": repair_file.markdown_content,
                "observed_at": repair_file.observed_at,
                "source": source,
            }
        )
        return SimpleNamespace(repaired=True)

    monkeypatch.setattr(note_content_reads.db, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(
        note_content_reads,
        "prepare_note_content_read_repair_with_default_repositories",
        fake_prepare_note_content_read_repair,
    )
    monkeypatch.setattr(
        note_content_reads,
        "run_note_content_read_repair_with_default_reconciler",
        fake_run_note_content_read_repair,
    )

    service = NoteContentQueryService(
        session_maker=tenant_session_maker,
        read_repair_file_reader=reader,
    )

    repaired = await service.reconcile_note_content_from_file(
        project_external_id="project-123",
        entity_external_id="note-456",
        source="read_repair",
    )

    assert repaired is True
    assert reader.targets == [target]
    assert reconciled == [
        {
            "markdown_content": "# Repaired\n",
            "observed_at": datetime(2026, 4, 13, 13, 0, tzinfo=UTC),
            "source": "read_repair",
        }
    ]


@pytest.mark.asyncio
async def test_note_content_read_repair_rereads_with_fresh_session(monkeypatch) -> None:
    tenant_session_maker = _session_maker()
    caller_session = cast(AsyncSession, object())
    reader = cast(Any, object())
    payload = cast(Any, SimpleNamespace(external_id="note-456"))
    resource = cast(Any, SimpleNamespace(uri="memory://note-456"))
    payload_sessions: list[AsyncSession | None] = []
    resource_sessions: list[AsyncSession | None] = []

    async def fake_get_note_entity_payload(
        *,
        project_external_id: str,
        entity_external_id: str,
        session: AsyncSession | None = None,
    ):
        assert project_external_id == "project-123"
        assert entity_external_id == "note-456"
        payload_sessions.append(session)
        return None if len(payload_sessions) == 1 else payload

    async def fake_get_note_resource(
        *,
        project_external_id: str,
        entity_external_id: str,
        session: AsyncSession | None = None,
    ):
        assert project_external_id == "project-123"
        assert entity_external_id == "note-456"
        resource_sessions.append(session)
        return None if len(resource_sessions) == 1 else resource

    async def fake_reconcile_note_content_from_file(
        *,
        project_external_id: str,
        entity_external_id: str,
        source: str,
    ) -> bool:
        assert project_external_id == "project-123"
        assert entity_external_id == "note-456"
        assert source == "read_repair"
        return True

    service = NoteContentQueryService(
        session_maker=tenant_session_maker,
        read_repair_file_reader=reader,
    )
    monkeypatch.setattr(service, "get_note_entity_payload", fake_get_note_entity_payload)
    monkeypatch.setattr(service, "get_note_resource", fake_get_note_resource)
    monkeypatch.setattr(
        service,
        "reconcile_note_content_from_file",
        fake_reconcile_note_content_from_file,
    )

    assert (
        await service.get_note_entity_payload_with_read_repair(
            project_external_id="project-123",
            entity_external_id="note-456",
            session=caller_session,
        )
    ) is payload
    assert (
        await service.get_note_resource_with_read_repair(
            project_external_id="project-123",
            entity_external_id="note-456",
            session=caller_session,
        )
    ) is resource

    assert payload_sessions == [caller_session, None]
    assert resource_sessions == [caller_session, None]


@pytest.mark.asyncio
async def test_note_content_mutation_service_delegates_create_to_core_runner(monkeypatch) -> None:
    tenant_session_maker = cast(async_sessionmaker[AsyncSession], FakeSessionMaker())
    dependencies = cast(AcceptedNoteMutationDependencies, object())
    user_profile_id = uuid4()
    data = EntitySchema(title="Created", directory="notes", content="# Created")
    returned = SimpleNamespace(status_code=201, payload={"ok": True})
    calls: list[tuple[AsyncSession, AcceptedNoteCreateMutation, object]] = []

    async def fake_runner(
        repository_session: AsyncSession,
        *,
        request: AcceptedNoteCreateMutation,
        dependencies: AcceptedNoteMutationDependencies,
    ):
        calls.append((repository_session, request, dependencies))
        return returned

    monkeypatch.setattr(note_content_writes, "run_accepted_note_create", fake_runner)

    service = NoteContentMutationService(
        session_maker=tenant_session_maker,
        mutation_dependencies=dependencies,
    )

    accepted = await service.create_note(
        project_external_id="project-123",
        data=data,
        user_profile_id=user_profile_id,
        source="api",
        actor_kind="mcp_client",
        actor_name="Claude Code",
    )

    assert accepted is returned
    assert len(calls) == 1
    session, request, received_dependencies = calls[0]
    assert isinstance(session, FakeSession)
    assert request.project_external_id == "project-123"
    assert request.data is data
    assert request.actor.user_profile_id == user_profile_id
    assert request.actor.kind == "mcp_client"
    assert request.actor.name == "Claude Code"
    assert received_dependencies is dependencies


@pytest.mark.asyncio
async def test_note_content_mutation_service_uses_injected_actor_resolver(monkeypatch) -> None:
    """A runtime adapter can replace route-passed actor values with its own
    request-derived identity without subclassing the service."""
    tenant_session_maker = cast(async_sessionmaker[AsyncSession], FakeSessionMaker())
    dependencies = cast(AcceptedNoteMutationDependencies, object())
    resolved_profile_id = uuid4()
    data = EntitySchema(title="Created", directory="notes", content="# Created")
    calls: list[AcceptedNoteCreateMutation] = []
    resolver_calls: list[tuple[str, NoteContentMutationActorContext]] = []

    async def fake_runner(
        repository_session: AsyncSession,
        *,
        request: AcceptedNoteCreateMutation,
        dependencies: AcceptedNoteMutationDependencies,
    ):
        calls.append(request)
        return SimpleNamespace(status_code=201, payload={"ok": True})

    monkeypatch.setattr(note_content_writes, "run_accepted_note_create", fake_runner)

    class RequestDerivedResolver:
        def resolve_mutation_actor(
            self,
            *,
            mutation_kind: str,
            requested: NoteContentMutationActorContext,
        ) -> NoteContentMutationActorContext:
            resolver_calls.append((mutation_kind, requested))
            return NoteContentMutationActorContext(
                user_profile_id=resolved_profile_id,
                source="web",
                actor_kind="mcp_client",
                actor_name="Resolved Actor",
            )

    service = NoteContentMutationService(
        session_maker=tenant_session_maker,
        mutation_dependencies=dependencies,
        actor_resolver=RequestDerivedResolver(),
    )

    await service.create_note(
        project_external_id="project-123",
        data=data,
        user_profile_id=None,
        source="api",
    )

    assert resolver_calls == [
        (
            "create",
            NoteContentMutationActorContext(
                user_profile_id=None,
                source="api",
                actor_kind=None,
                actor_name=None,
            ),
        )
    ]
    request = calls[0]
    assert request.actor.user_profile_id == resolved_profile_id
    assert request.actor.kind == "mcp_client"
    assert request.actor.name == "Resolved Actor"
    assert request.source == "web"


@pytest.mark.asyncio
async def test_note_content_mutation_service_delegates_remaining_methods_to_core_runners(
    monkeypatch,
) -> None:
    tenant_session_maker = cast(async_sessionmaker[AsyncSession], FakeSessionMaker())
    dependencies = cast(AcceptedNoteMutationDependencies, object())
    user_profile_id = uuid4()
    update_data = EntitySchema(title="Updated", directory="notes", content="# Updated")
    edit_data = EditEntityRequest(
        operation="find_replace",
        find_text="# Old",
        content="# New",
        expected_replacements=1,
    )
    returned = SimpleNamespace(status_code=200, payload={"ok": True})
    calls: list[tuple[str, AsyncSession, object, object]] = []

    def runner(name: str):
        async def fake_runner(
            repository_session: AsyncSession,
            *,
            request: object,
            dependencies: object,
        ):
            calls.append((name, repository_session, request, dependencies))
            return returned

        return fake_runner

    monkeypatch.setattr(note_content_writes, "run_accepted_note_update", runner("update"))
    monkeypatch.setattr(note_content_writes, "run_accepted_note_edit", runner("edit"))
    monkeypatch.setattr(note_content_writes, "run_accepted_note_move", runner("move"))
    monkeypatch.setattr(note_content_writes, "run_accepted_note_delete", runner("delete"))

    service = NoteContentMutationService(
        session_maker=tenant_session_maker,
        mutation_dependencies=dependencies,
    )

    assert (
        await service.update_note(
            project_external_id="project-123",
            entity_external_id="entity-123",
            data=update_data,
            user_profile_id=user_profile_id,
            source="api",
            actor_kind="mcp_client",
            actor_name="Claude Code",
        )
    ) is returned
    assert (
        await service.edit_note(
            project_external_id="project-123",
            entity_external_id="entity-123",
            data=edit_data,
            user_profile_id=user_profile_id,
            source="mcp",
            actor_kind="mcp_client",
            actor_name="Claude Code",
        )
    ) is returned
    assert (
        await service.move_note(
            project_external_id="project-123",
            entity_external_id="entity-123",
            destination_path="archive/entity.md",
            user_profile_id=user_profile_id,
            source="api",
            actor_kind="mcp_client",
            actor_name="Claude Code",
        )
    ) is returned
    assert (
        await service.delete_note(
            project_external_id="project-123",
            entity_external_id="entity-123",
        )
    ) is returned

    assert [call[0] for call in calls] == ["update", "edit", "move", "delete"]
    for _, session, _, received_dependencies in calls:
        assert isinstance(session, FakeSession)
        assert received_dependencies is dependencies

    update_request = calls[0][2]
    assert isinstance(update_request, AcceptedNoteUpdateMutation)
    assert update_request.data is update_data
    assert update_request.actor.user_profile_id == user_profile_id
    assert update_request.actor.kind == "mcp_client"
    assert update_request.actor.name == "Claude Code"

    edit_request = calls[1][2]
    assert isinstance(edit_request, AcceptedNoteEditMutation)
    assert edit_request.data is edit_data
    assert edit_request.source == "mcp"

    move_request = calls[2][2]
    assert isinstance(move_request, AcceptedNoteMoveMutation)
    assert move_request.destination_path == "archive/entity.md"

    delete_request = calls[3][2]
    assert isinstance(delete_request, AcceptedNoteDeleteMutation)
    assert delete_request.project_external_id == "project-123"
    assert delete_request.entity_external_id == "entity-123"


@pytest.mark.asyncio
async def test_note_content_mutation_service_maps_core_rejections(monkeypatch) -> None:
    tenant_session_maker = cast(async_sessionmaker[AsyncSession], FakeSessionMaker())

    async def rejecting_runner(
        _repository_session: AsyncSession,
        *,
        request: AcceptedNoteCreateMutation,
        dependencies: AcceptedNoteMutationDependencies,
    ):
        raise AcceptedNoteMutationRejected(
            AcceptedNoteMutationRejection(
                kind=AcceptedNoteMutationRejectKind.conflict,
                detail="A note with this external_id already exists.",
            )
        )

    monkeypatch.setattr(note_content_writes, "run_accepted_note_create", rejecting_runner)

    service = NoteContentMutationService(
        session_maker=tenant_session_maker,
        mutation_dependencies=cast(AcceptedNoteMutationDependencies, object()),
    )

    with pytest.raises(NoteContentMutationServiceError) as exc_info:
        await service.create_note(
            project_external_id="project-123",
            data=EntitySchema(title="Created", directory="notes", content="# Created"),
            user_profile_id=None,
            source="api",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "A note with this external_id already exists."


def test_rejection_kinds_own_route_status_behavior() -> None:
    assert AcceptedNoteMutationRejectKind.bad_request.http_status_code == 400
    assert AcceptedNoteMutationRejectKind.conflict.http_status_code == 409
    assert AcceptedNoteMutationRejectKind.not_found.http_status_code == 404
    assert AcceptedNoteMutationRejectKind.unsupported_media_type.http_status_code == 415
    assert DirectoryDeleteRejectKind.bad_request.http_status_code == 400
    assert DirectoryDeleteRejectKind.not_found.http_status_code == 404


@pytest.mark.asyncio
async def test_directory_delete_service_uses_injected_runtime_and_session_maker(
    session_maker,
) -> None:
    enqueuer = FakeDirectoryFileDeleteEnqueuer()
    # Real session_maker so scoped_session can enable SQLite FK cascades; the fake
    # store ignores the session and returns canned snapshots.
    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=FakeDirectoryDeleteStore(),
            file_delete_enqueuer=enqueuer,
        ),
    )

    status_code, payload = await service.delete_directory(
        project_external_id="project-123",
        directory="/notes/",
    )

    assert status_code == 200
    assert payload["file_delete_status"] == "pending"
    assert payload["deleted_files"] == ["notes/example.md"]
    assert enqueuer.requests == [
        RuntimeNoteFileDeleteJobRequest(
            project_id=3,
            entity_id=7,
            file_path="notes/example.md",
            file_checksum="note-sha",
        )
    ]


def test_directory_delete_service_rejects_project_traversal() -> None:
    try:
        DirectoryDeleteService.normalize_directory_path("notes/../other")
    except DirectoryDeleteServiceError as error:
        assert error.status_code == 400
        assert error.detail == "Invalid directory path"
    else:  # pragma: no cover
        raise AssertionError("expected DirectoryDeleteServiceError")
