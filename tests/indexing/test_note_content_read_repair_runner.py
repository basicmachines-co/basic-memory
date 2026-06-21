from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.note_content_read_repair_runner import (
    NoteContentReadRepairFile,
    NoteContentReadRepairPreflight,
    NoteContentReadRepairReconcilerProvider,
    NoteContentReadRepairRepositories,
    NoteContentReadView,
    NoteContentReadRepositories,
    NoteContentReadRepairTarget,
    apply_note_content_read_repair,
    load_note_content_read_view,
    note_content_resource_from_read_view,
    note_content_response_payload_from_read_view,
    prepare_note_content_read_repair,
    run_note_content_read_repair,
)
from basic_memory.runtime import (
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentReadRepairStatus,
    RuntimeNoteContentResource,
)


@dataclass(frozen=True, slots=True)
class _Project:
    id: int
    path: str


@dataclass(frozen=True, slots=True)
class _Entity:
    id: int
    content_type: str
    file_path: str
    external_id: str = "note-456"
    title: str = "Read note"
    note_type: str = "note"
    entity_metadata: dict[str, object] | None = None
    permalink: str | None = "main/notes/read"
    content: str | None = None
    observations: tuple[object, ...] = ()
    relations: tuple[object, ...] = ()
    created_at: datetime = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
    updated_at: datetime = datetime(2026, 4, 13, 12, 5, tzinfo=UTC)
    created_by: str | None = "creator"
    last_updated_by: str | None = "editor"


@dataclass(frozen=True, slots=True)
class _NoteContent:
    markdown_content: str
    db_version: int = 4
    db_checksum: str = "db-checksum"
    file_version: int | None = 3
    file_checksum: str | None = "file-checksum"
    file_write_status: str = "synced"
    last_source: str | None = "api"
    file_updated_at: datetime | None = datetime(2026, 4, 13, 13, 0, tzinfo=UTC)
    last_materialization_error: str | None = None


class _ProjectRepository:
    def __init__(self, project: _Project | None) -> None:
        self.project = project
        self.external_ids: list[str] = []

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
    ) -> _Project | None:
        assert session is not None
        self.external_ids.append(external_id)
        return self.project


class _EntityRepository:
    def __init__(self, entity: _Entity | None) -> None:
        self.entity = entity
        self.external_ids: list[str] = []

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
    ) -> _Entity | None:
        assert session is not None
        self.external_ids.append(external_id)
        return self.entity


class _NoteContentRepository:
    def __init__(self, note_content: _NoteContent | None) -> None:
        self.note_content = note_content
        self.entity_ids: list[int] = []

    async def get_by_entity_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> _NoteContent | None:
        assert session is not None
        self.entity_ids.append(entity_id)
        return self.note_content


@dataclass(frozen=True, slots=True)
class _ReadRepositories:
    project_repository_result: _ProjectRepository
    entity_repository_result: _EntityRepository
    note_content_repository_result: _NoteContentRepository

    def project_repository(self) -> _ProjectRepository:
        return self.project_repository_result

    def entity_repository(self, project_id: int) -> _EntityRepository:
        _ = project_id
        return self.entity_repository_result

    def note_content_repository(self, project_id: int) -> _NoteContentRepository:
        _ = project_id
        return self.note_content_repository_result


class _FileReader:
    def __init__(self, repair_file: NoteContentReadRepairFile | None) -> None:
        self.repair_file = repair_file
        self.targets: list[NoteContentReadRepairTarget[_Project, _Entity]] = []

    async def read_note_content_repair_file(
        self,
        target: NoteContentReadRepairTarget[_Project, _Entity],
    ) -> NoteContentReadRepairFile | None:
        self.targets.append(target)
        return self.repair_file


class _NeverReconciler:
    async def reconcile(
        self,
        *,
        entity: _Entity,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
    ) -> None:
        raise AssertionError("unexpected read repair reconciliation")


@dataclass(frozen=True, slots=True)
class _FailingReconcilerProvider:
    message: str

    def reconciler(
        self,
        project_id: int,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> _NeverReconciler:
        _ = project_id
        _ = session_maker
        pytest.fail(self.message)


def test_note_content_read_providers_name_repository_and_repair_behavior() -> None:
    """Read repair should use behavior providers, not Callable factory aliases."""

    project_repository = _ProjectRepository(_Project(id=7, path="/app/data/main"))
    entity_repository = _EntityRepository(
        _Entity(id=42, content_type="text/markdown", file_path="notes/read.md")
    )
    note_content_repository = _NoteContentRepository(_NoteContent(markdown_content="# Read\n"))
    test_session_maker = cast(async_sessionmaker[AsyncSession], object())

    class FakeReconciler:
        async def reconcile(
            self,
            *,
            entity: _Entity,
            markdown_content: str,
            observed_at: datetime | None,
            source: str,
        ) -> None:
            return None

    class ReadRepositories:
        def project_repository(self) -> _ProjectRepository:
            return project_repository

        def entity_repository(self, project_id: int) -> _EntityRepository:
            assert project_id == 7
            return entity_repository

        def note_content_repository(self, project_id: int) -> _NoteContentRepository:
            assert project_id == 7
            return note_content_repository

    class ReconcilerProvider:
        def reconciler(
            self,
            project_id: int,
            session_maker: async_sessionmaker[AsyncSession],
        ) -> FakeReconciler:
            assert project_id == 7
            assert session_maker is test_session_maker
            return FakeReconciler()

    read_repositories: NoteContentReadRepositories[_Project, _Entity, _NoteContent] = (
        ReadRepositories()
    )
    repair_repositories: NoteContentReadRepairRepositories[_Project, _Entity, _NoteContent] = (
        ReadRepositories()
    )
    reconciler_provider: NoteContentReadRepairReconcilerProvider[_Entity] = ReconcilerProvider()

    assert read_repositories.project_repository() is project_repository
    assert read_repositories.entity_repository(7) is entity_repository
    assert repair_repositories.note_content_repository(7) is note_content_repository
    assert reconciler_provider.reconciler(7, test_session_maker) is not None


@pytest.mark.asyncio
async def test_load_note_content_read_view_returns_markdown_entity_with_content() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/read.md")
    note_content = _NoteContent(markdown_content="# Read\n")
    project_repository = _ProjectRepository(project)
    entity_repository = _EntityRepository(entity)
    note_content_repository = _NoteContentRepository(note_content)

    view = await load_note_content_read_view(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=project_repository,
            entity_repository_result=entity_repository,
            note_content_repository_result=note_content_repository,
        ),
    )

    assert view == NoteContentReadView(entity=entity, note_content=note_content)
    assert project_repository.external_ids == ["project-123"]
    assert entity_repository.external_ids == ["note-456"]
    assert note_content_repository.entity_ids == [42]


def test_note_content_response_payload_from_read_view_returns_accepted_note_response() -> None:
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/read.md")
    note_content = _NoteContent(markdown_content="# Read\n")

    payload = note_content_response_payload_from_read_view(
        NoteContentReadView(entity=entity, note_content=note_content)
    )

    assert isinstance(payload, RuntimeAcceptedNoteResponse)
    assert payload.external_id == "note-456"
    assert payload.markdown_content == "# Read\n"
    assert payload.db_version == 4
    assert payload.db_checksum == "db-checksum"
    assert payload.file_write_status == "synced"


def test_note_content_response_payload_from_read_view_returns_entity_payload_for_non_markdown() -> (
    None
):
    entity = _Entity(
        id=42,
        content_type="image/png",
        file_path="images/diagram.png",
        title="diagram.png",
        note_type="file",
        permalink="main/images/diagram",
    )

    payload = note_content_response_payload_from_read_view(
        NoteContentReadView(entity=entity, note_content=None)
    )

    assert payload is not None
    assert not isinstance(payload, RuntimeAcceptedNoteResponse)
    payload_dict = dict(payload)
    assert payload_dict["external_id"] == "note-456"
    assert payload_dict["title"] == "diagram.png"
    assert payload_dict["note_type"] == "file"
    assert payload_dict["content_type"] == "image/png"
    assert payload_dict["file_path"] == "images/diagram.png"
    assert "db_version" not in payload_dict


def test_note_content_resource_from_read_view_returns_accepted_markdown_resource() -> None:
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/read.md")
    note_content = _NoteContent(markdown_content="# Read\n")

    resource = note_content_resource_from_read_view(
        NoteContentReadView(entity=entity, note_content=note_content)
    )

    assert isinstance(resource, RuntimeNoteContentResource)
    assert resource.content == "# Read\n"
    assert resource.content_type == "text/markdown"


def test_note_content_read_payload_helpers_return_none_for_missing_view_or_content() -> None:
    markdown_without_content = NoteContentReadView(
        entity=_Entity(id=42, content_type="text/markdown", file_path="notes/read.md"),
        note_content=None,
    )

    assert note_content_response_payload_from_read_view(None) is None
    assert note_content_response_payload_from_read_view(markdown_without_content) is None
    assert note_content_resource_from_read_view(None) is None
    assert note_content_resource_from_read_view(markdown_without_content) is None


@pytest.mark.asyncio
async def test_load_note_content_read_view_returns_none_when_project_is_missing() -> None:
    session = cast(AsyncSession, object())
    project_repository = _ProjectRepository(None)

    view = await load_note_content_read_view(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=project_repository,
            entity_repository_result=_EntityRepository(None),
            note_content_repository_result=_NoteContentRepository(None),
        ),
    )

    assert view is None
    assert project_repository.external_ids == ["project-123"]


@pytest.mark.asyncio
async def test_load_note_content_read_view_skips_note_lookup_for_non_markdown() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="image/png", file_path="images/diagram.png")

    view = await load_note_content_read_view(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=_ProjectRepository(project),
            entity_repository_result=_EntityRepository(entity),
            note_content_repository_result=_NoteContentRepository(None),
        ),
    )

    assert view == NoteContentReadView(entity=entity, note_content=None)


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_returns_storage_target_for_missing_row() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    project_repository = _ProjectRepository(project)
    entity_repository = _EntityRepository(entity)
    note_content_repository = _NoteContentRepository(None)

    preflight = await prepare_note_content_read_repair(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=project_repository,
            entity_repository_result=entity_repository,
            note_content_repository_result=note_content_repository,
        ),
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.read_file
    assert preflight.should_read_file
    assert preflight.require_target() == NoteContentReadRepairTarget(
        project=project,
        entity=entity,
    )
    assert project_repository.external_ids == ["project-123"]
    assert entity_repository.external_ids == ["note-456"]
    assert note_content_repository.entity_ids == [42]


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_reports_existing_row_as_repaired() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    note_content_repository = _NoteContentRepository(_NoteContent(markdown_content="# Present\n"))

    preflight = await prepare_note_content_read_repair(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=_ProjectRepository(project),
            entity_repository_result=_EntityRepository(entity),
            note_content_repository_result=note_content_repository,
        ),
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.already_present
    assert preflight.repaired
    assert not preflight.should_read_file
    with pytest.raises(RuntimeError, match="does not contain a target"):
        preflight.require_target()


@pytest.mark.asyncio
async def test_prepare_note_content_read_repair_skips_note_lookup_for_non_markdown() -> None:
    session = cast(AsyncSession, object())
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="image/png", file_path="images/diagram.png")

    preflight = await prepare_note_content_read_repair(
        session,
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=_ProjectRepository(project),
            entity_repository_result=_EntityRepository(entity),
            note_content_repository_result=_NoteContentRepository(None),
        ),
    )

    assert preflight.status is RuntimeNoteContentReadRepairStatus.entity_missing
    assert not preflight.repaired
    assert not preflight.should_read_file


@pytest.mark.asyncio
async def test_apply_note_content_read_repair_uses_project_reconciler() -> None:
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    target = NoteContentReadRepairTarget(project=project, entity=entity)
    test_session_maker = cast(async_sessionmaker[AsyncSession], object())
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    calls: list[tuple[_Entity, str, datetime | None, str]] = []
    factory_calls: list[tuple[int, async_sessionmaker[AsyncSession]]] = []

    class FakeReconciler:
        async def reconcile(
            self,
            *,
            entity: _Entity,
            markdown_content: str,
            observed_at: datetime | None,
            source: str,
        ) -> None:
            calls.append((entity, markdown_content, observed_at, source))

    class FakeReconcilerProvider:
        def reconciler(
            self,
            project_id: int,
            session_maker: async_sessionmaker[AsyncSession],
        ) -> FakeReconciler:
            factory_calls.append((project_id, session_maker))
            return FakeReconciler()

    await apply_note_content_read_repair(
        target,
        session_maker=test_session_maker,
        markdown_content="# Repaired\n",
        observed_at=observed_at,
        source="read_repair",
        reconciler_provider=FakeReconcilerProvider(),
    )

    assert factory_calls == [(7, test_session_maker)]
    assert calls == [(entity, "# Repaired\n", observed_at, "read_repair")]


@pytest.mark.asyncio
async def test_run_note_content_read_repair_returns_preflight_status_without_file_read() -> None:
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    preflight = await prepare_note_content_read_repair(
        cast(AsyncSession, object()),
        project_external_id="project-123",
        entity_external_id="note-456",
        repositories=_ReadRepositories(
            project_repository_result=_ProjectRepository(project),
            entity_repository_result=_EntityRepository(entity),
            note_content_repository_result=_NoteContentRepository(
                _NoteContent(markdown_content="# Present\n")
            ),
        ),
    )

    run = await run_note_content_read_repair(
        preflight,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_reader=None,
        source="read_repair",
        reconciler_provider=_FailingReconcilerProvider(
            "already-present repair should not reconcile"
        ),
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.already_present
    assert run.repaired


@pytest.mark.asyncio
async def test_run_note_content_read_repair_reports_missing_file() -> None:
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    target = NoteContentReadRepairTarget(project=project, entity=entity)
    file_reader = _FileReader(None)

    run = await run_note_content_read_repair(
        preflight=NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=target,
        ),
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_reader=file_reader,
        source="read_repair",
        reconciler_provider=_FailingReconcilerProvider("missing files should not reconcile"),
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.file_missing
    assert not run.repaired
    assert file_reader.targets == [target]


@pytest.mark.asyncio
async def test_run_note_content_read_repair_reports_empty_file() -> None:
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    target = NoteContentReadRepairTarget(project=project, entity=entity)

    run = await run_note_content_read_repair(
        preflight=NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=target,
        ),
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_reader=_FileReader(NoteContentReadRepairFile(None, observed_at=None)),
        source="read_repair",
        reconciler_provider=_FailingReconcilerProvider("empty files should not reconcile"),
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.empty_file
    assert not run.repaired


@pytest.mark.asyncio
async def test_run_note_content_read_repair_applies_observed_markdown() -> None:
    project = _Project(id=7, path="/app/data/main")
    entity = _Entity(id=42, content_type="text/markdown", file_path="notes/repair.md")
    target = NoteContentReadRepairTarget(project=project, entity=entity)
    test_session_maker = cast(async_sessionmaker[AsyncSession], object())
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    calls: list[tuple[_Entity, str, datetime | None, str]] = []

    class FakeReconciler:
        async def reconcile(
            self,
            *,
            entity: _Entity,
            markdown_content: str,
            observed_at: datetime | None,
            source: str,
        ) -> None:
            calls.append((entity, markdown_content, observed_at, source))

    class FakeReconcilerProvider:
        def reconciler(
            self,
            project_id: int,
            session_maker: async_sessionmaker[AsyncSession],
        ) -> FakeReconciler:
            assert project_id == 7
            assert session_maker is test_session_maker
            return FakeReconciler()

    run = await run_note_content_read_repair(
        preflight=NoteContentReadRepairPreflight(
            status=RuntimeNoteContentReadRepairStatus.read_file,
            target=target,
        ),
        session_maker=test_session_maker,
        file_reader=_FileReader(NoteContentReadRepairFile("# Repaired\n", observed_at=observed_at)),
        source="read_repair",
        reconciler_provider=FakeReconcilerProvider(),
    )

    assert run.status is RuntimeNoteContentReadRepairStatus.repaired
    assert run.repaired
    assert calls == [(entity, "# Repaired\n", observed_at, "read_repair")]
