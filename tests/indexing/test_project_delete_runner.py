"""Tests for portable project-delete cleanup orchestration."""

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from basic_memory.indexing.project_delete_runner import (
    DefaultProjectDeleteRepositories,
    ProjectDeletePreflightResult,
    ProjectDeleteRepositories,
    RepositoryProjectDeletePreflight,
    RepositoryProjectHardDeleter,
    run_project_delete,
)
from basic_memory.models import Base as BasicMemoryBase
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.runtime import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectDeleteResult,
    RuntimeProjectFileSnapshot,
)


@pytest_asyncio.fixture
async def project_delete_session_maker() -> AsyncGenerator[
    async_sessionmaker[AsyncSession],
    None,
]:
    """Create an isolated Basic Memory tenant database."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(BasicMemoryBase.metadata.create_all)

    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


class FakeProjectDeletePreflight:
    def __init__(self, result: ProjectDeletePreflightResult) -> None:
        self.result = result
        self.requests: list[RuntimeProjectDeleteJobRequest] = []

    async def prepare_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectDeletePreflightResult:
        self.requests.append(request)
        return self.result


class FakeProjectFileDeleter:
    def __init__(self, results: list[RuntimeFileDeleteResult]) -> None:
        self.results = results
        self.requests: list[RuntimeNoteFileDeleteJobRequest] = []

    async def delete_project_file(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult:
        self.requests.append(request)
        return self.results.pop(0)


class FakeProjectHardDeleter:
    def __init__(self, *, deleted: bool) -> None:
        self.deleted = deleted
        self.requests: list[RuntimeProjectDeleteJobRequest] = []

    async def hard_delete_project(self, request: RuntimeProjectDeleteJobRequest) -> bool:
        self.requests.append(request)
        return self.deleted


class FakeProjectDeleteRepository:
    def __init__(self, *, deleted: bool) -> None:
        self.deleted = deleted
        self.entity_ids: list[int] = []

    async def delete(self, session: AsyncSession, entity_id: int) -> bool:
        self.entity_ids.append(entity_id)
        return self.deleted


@dataclass(frozen=True, slots=True)
class FakeProjectDeleteRepositories:
    repository: FakeProjectDeleteRepository

    def project_repository(self) -> FakeProjectDeleteRepository:
        return self.repository


@dataclass(slots=True)
class RecordingProjectDeleteSessionProvider:
    opened_session_makers: list[async_sessionmaker[AsyncSession]]

    def open_session(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AbstractAsyncContextManager[AsyncSession]:
        self.opened_session_makers.append(session_maker)
        return session_maker()


def project_delete_request(
    *,
    project_id: int = 101,
    delete_notes: bool = True,
) -> RuntimeProjectDeleteJobRequest:
    return RuntimeProjectDeleteJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=project_id,
        project_external_id="project-main",
        project_name="Main",
        project_path="basic-memory",
        delete_notes=delete_notes,
    )


def project_file_snapshot(
    *,
    entity_id: int,
    file_path: str,
    file_checksum: str | None,
) -> RuntimeProjectFileSnapshot:
    return RuntimeProjectFileSnapshot(
        entity_id=entity_id,
        file_path=file_path,
        file_checksum=file_checksum,
    )


async def create_project_with_note(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    is_active: bool,
) -> Project:
    async with session_maker() as session:
        project = Project(
            name="Main",
            path="basic-memory",
            permalink="main",
            external_id="project-main",
            is_active=is_active,
            is_default=False,
        )
        session.add(project)
        await session.flush()
        entity = Entity(
            title="Alpha",
            note_type="note",
            entity_metadata={"title": "Alpha"},
            content_type="text/markdown",
            project_id=project.id,
            permalink="alpha",
            file_path="notes/a.md",
            checksum="entity-sum",
            mtime=datetime(2026, 5, 22, 12, 0, tzinfo=UTC).timestamp(),
            size=42,
        )
        session.add(entity)
        await session.flush()
        session.add(
            NoteContent(
                entity_id=entity.id,
                project_id=project.id,
                external_id=entity.external_id,
                file_path=entity.file_path,
                markdown_content="# Alpha\n",
                db_version=1,
                db_checksum="db-sum",
                file_version=1,
                file_checksum="file-sum",
                file_write_status="synced",
                file_updated_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            )
        )
        await session.commit()
        return project


@pytest.mark.asyncio
async def test_repository_project_delete_preflight_snapshots_inactive_project_files(
    project_delete_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    project = await create_project_with_note(project_delete_session_maker, is_active=False)
    request = project_delete_request(project_id=project.id)

    result = await RepositoryProjectDeletePreflight(
        session_maker=project_delete_session_maker
    ).prepare_project_delete(request)

    assert result == ProjectDeletePreflightResult.ready(
        [
            RuntimeProjectFileSnapshot(
                entity_id=1,
                file_path="notes/a.md",
                file_checksum="file-sum",
            )
        ]
    )


@pytest.mark.asyncio
async def test_repository_project_delete_preflight_uses_session_provider(
    project_delete_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    project = await create_project_with_note(project_delete_session_maker, is_active=False)
    request = project_delete_request(project_id=project.id)
    session_provider = RecordingProjectDeleteSessionProvider(opened_session_makers=[])

    result = await RepositoryProjectDeletePreflight(
        session_maker=project_delete_session_maker,
        session_provider=session_provider,
    ).prepare_project_delete(request)

    assert result.file_snapshots
    assert session_provider.opened_session_makers == [project_delete_session_maker]


@pytest.mark.asyncio
async def test_repository_project_delete_preflight_skips_active_project(
    project_delete_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    project = await create_project_with_note(project_delete_session_maker, is_active=True)
    request = project_delete_request(project_id=project.id)

    result = await RepositoryProjectDeletePreflight(
        session_maker=project_delete_session_maker
    ).prepare_project_delete(request)

    assert result == ProjectDeletePreflightResult.terminal(
        RuntimeProjectDeleteResult(
            project_id=project.id,
            project_external_id="project-main",
            status=RuntimeDeleteStatus.skipped,
            deleted_project=False,
            deleted_files=0,
            skipped_files=0,
            missing_files=0,
            reason=f"project is active: {project.id}",
        )
    )


@pytest.mark.asyncio
async def test_repository_project_hard_deleter_deletes_project(
    project_delete_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    project = await create_project_with_note(project_delete_session_maker, is_active=False)
    request = project_delete_request(project_id=project.id)

    deleted = await RepositoryProjectHardDeleter(
        session_maker=project_delete_session_maker
    ).hard_delete_project(request)

    async with project_delete_session_maker() as session:
        stored_project = await session.get(Project, project.id)

    assert deleted is True
    assert stored_project is None


@pytest.mark.asyncio
async def test_repository_project_hard_deleter_uses_repository_provider(
    project_delete_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    request = project_delete_request(project_id=42)
    repository = FakeProjectDeleteRepository(deleted=False)
    repositories: ProjectDeleteRepositories = FakeProjectDeleteRepositories(repository)

    deleted = await RepositoryProjectHardDeleter(
        session_maker=project_delete_session_maker,
        repositories=repositories,
    ).hard_delete_project(request)

    assert deleted is False
    assert repository.entity_ids == [42]


def test_default_project_delete_repositories_builds_project_repository() -> None:
    repositories: ProjectDeleteRepositories = DefaultProjectDeleteRepositories()

    assert repositories.project_repository().__class__.__name__ == "ProjectRepository"


@pytest.mark.asyncio
async def test_run_project_delete_deletes_files_then_hard_deletes_project() -> None:
    request = project_delete_request()
    file_deleter = FakeProjectFileDeleter(
        [
            RuntimeFileDeleteResult.deleted(entity_id=42, file_path="notes/a.md"),
            RuntimeFileDeleteResult.changed_before_delete(
                entity_id=43,
                file_path="notes/b.md",
            ),
        ]
    )
    hard_deleter = FakeProjectHardDeleter(deleted=True)

    result = await run_project_delete(
        request,
        preflight=FakeProjectDeletePreflight(
            ProjectDeletePreflightResult.ready(
                [
                    project_file_snapshot(
                        entity_id=42,
                        file_path="notes/a.md",
                        file_checksum="file-sum-a",
                    ),
                    project_file_snapshot(
                        entity_id=43,
                        file_path="notes/b.md",
                        file_checksum="file-sum-b",
                    ),
                ]
            )
        ),
        file_deleter=file_deleter,
        hard_deleter=hard_deleter,
    )

    assert result == RuntimeProjectDeleteResult(
        project_id=101,
        project_external_id="project-main",
        status=RuntimeDeleteStatus.deleted,
        deleted_project=True,
        deleted_files=1,
        skipped_files=1,
        missing_files=0,
        reason="project deleted: 101",
    )
    assert file_deleter.requests == [
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=request.tenant_id,
            project_id=101,
            entity_id=42,
            file_path="notes/a.md",
            file_checksum="file-sum-a",
        ),
        RuntimeNoteFileDeleteJobRequest(
            tenant_id=request.tenant_id,
            project_id=101,
            entity_id=43,
            file_path="notes/b.md",
            file_checksum="file-sum-b",
        ),
    ]
    assert hard_deleter.requests == [request]


@pytest.mark.asyncio
async def test_run_project_delete_returns_terminal_preflight_result() -> None:
    request = project_delete_request()
    terminal_result = RuntimeProjectDeleteResult(
        project_id=101,
        project_external_id="project-main",
        status=RuntimeDeleteStatus.skipped,
        deleted_project=False,
        deleted_files=0,
        skipped_files=0,
        missing_files=0,
        reason="project is active: 101",
    )
    file_deleter = FakeProjectFileDeleter([])
    hard_deleter = FakeProjectHardDeleter(deleted=True)

    result = await run_project_delete(
        request,
        preflight=FakeProjectDeletePreflight(
            ProjectDeletePreflightResult.terminal(terminal_result)
        ),
        file_deleter=file_deleter,
        hard_deleter=hard_deleter,
    )

    assert result == terminal_result
    assert file_deleter.requests == []
    assert hard_deleter.requests == []


@pytest.mark.asyncio
async def test_run_project_delete_preserves_file_counts_when_project_disappears() -> None:
    request = project_delete_request()
    file_deleter = FakeProjectFileDeleter(
        [
            RuntimeFileDeleteResult.already_absent(
                entity_id=42,
                file_path="notes/a.md",
            )
        ]
    )

    result = await run_project_delete(
        request,
        preflight=FakeProjectDeletePreflight(
            ProjectDeletePreflightResult.ready(
                [
                    project_file_snapshot(
                        entity_id=42,
                        file_path="notes/a.md",
                        file_checksum="file-sum",
                    )
                ]
            )
        ),
        file_deleter=file_deleter,
        hard_deleter=FakeProjectHardDeleter(deleted=False),
    )

    assert result == RuntimeProjectDeleteResult(
        project_id=101,
        project_external_id="project-main",
        status=RuntimeDeleteStatus.missing,
        deleted_project=False,
        deleted_files=0,
        skipped_files=0,
        missing_files=1,
        reason="project already absent: 101",
    )
