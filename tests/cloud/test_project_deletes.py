from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from basic_memory.services.project_deletes import (
    ProjectDeleteAcceptanceError,
    ProjectDeleteAcceptanceRequest,
    ProjectDeleteAcceptanceService,
)
from basic_memory.models import Base as BasicMemoryBase
from basic_memory.models import Project
from basic_memory.runtime.jobs import RuntimeJobId, RuntimeProjectDeleteJobRequest
from basic_memory.schemas.project_info import ProjectItem
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def tenant_session_maker() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
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


async def create_project(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    name: str = "Main",
    permalink: str = "main",
    external_id: str = "project-main",
    path: str = "basic-memory",
    # None mirrors the nullable column default; the accepted response maps it to False.
    is_default: bool | None = None,
    created_at: datetime | None = None,
) -> Project:
    async with session_maker() as session:
        project = Project(
            name=name,
            path=path,
            permalink=permalink,
            external_id=external_id,
            is_active=True,
            is_default=is_default,
        )
        # Pin created_at when a test needs a deterministic promotion order.
        if created_at is not None:
            project.created_at = created_at
        session.add(project)
        await session.commit()
        return project


class RecordingProjectDeleteEnqueuer:
    def __init__(self, job_id: RuntimeJobId = 123) -> None:
        self.job_id = job_id
        self.requests: list[RuntimeProjectDeleteJobRequest] = []

    async def enqueue_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> RuntimeJobId:
        self.requests.append(request)
        return self.job_id


class FailingProjectDeleteEnqueuer:
    async def enqueue_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> RuntimeJobId:
        raise RuntimeError("queue unavailable")


@pytest.mark.asyncio
async def test_project_delete_acceptance_soft_deletes_and_queues_runtime_request(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    project = await create_project(tenant_session_maker)
    enqueuer = RecordingProjectDeleteEnqueuer()
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=enqueuer,
    )

    result = await service.delete_project(
        ProjectDeleteAcceptanceRequest(
            project_external_id="project-main",
            delete_notes=True,
        )
    )

    async with tenant_session_maker() as session:
        stored_project = await session.get(Project, project.id)

    assert stored_project is not None
    assert stored_project.is_active is False
    assert enqueuer.requests == [
        RuntimeProjectDeleteJobRequest(
            project_id=project.id,
            project_external_id="project-main",
            project_name="Main",
            project_path="basic-memory",
            delete_notes=True,
        )
    ]
    assert result.to_response_payload()["job_id"] == "123"
    assert result.to_response_payload()["file_delete_status"] == "pending"
    assert result.old_project == ProjectItem(
        id=project.id,
        external_id="project-main",
        name="Main",
        path="basic-memory",
        is_default=False,
    )


@pytest.mark.asyncio
async def test_project_delete_acceptance_rejects_deleting_the_only_project(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    # The default project can only be deleted when another active project can
    # inherit the flag. A sole project has no replacement, so the delete is
    # rejected before any soft delete happens.
    project = await create_project(tenant_session_maker, is_default=True)
    enqueuer = RecordingProjectDeleteEnqueuer()
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=enqueuer,
    )

    with pytest.raises(ProjectDeleteAcceptanceError) as exc_info:
        await service.delete_project(
            ProjectDeleteAcceptanceRequest(
                project_external_id="project-main",
                delete_notes=True,
            )
        )

    async with tenant_session_maker() as session:
        stored_project = await session.get(Project, project.id)

    assert exc_info.value.status_code == 400
    assert "only project" in exc_info.value.detail
    assert stored_project is not None
    assert stored_project.is_active is True
    assert enqueuer.requests == []


@pytest.mark.asyncio
async def test_project_delete_acceptance_promotes_replacement_default(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    # Deleting the default project hands the flag to another active project so
    # the workspace always resolves a default for project-less writes.
    default_project = await create_project(
        tenant_session_maker,
        is_default=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    sibling = await create_project(
        tenant_session_maker,
        name="Notes",
        permalink="notes",
        external_id="project-notes",
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    enqueuer = RecordingProjectDeleteEnqueuer()
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=enqueuer,
    )

    result = await service.delete_project(
        ProjectDeleteAcceptanceRequest(
            project_external_id="project-main",
            delete_notes=False,
        )
    )

    async with tenant_session_maker() as session:
        deleted = await session.get(Project, default_project.id)
        promoted = await session.get(Project, sibling.id)

    assert deleted is not None
    assert deleted.is_active is False
    assert not deleted.is_default
    assert promoted is not None
    assert promoted.is_default is True
    assert len(enqueuer.requests) == 1
    # The accepted response still describes the project that was removed.
    assert result.old_project.external_id == "project-main"


@pytest.mark.asyncio
async def test_project_delete_acceptance_promotes_oldest_active_project(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    # When several projects could inherit the default, the oldest active one
    # wins so the promotion is deterministic.
    await create_project(
        tenant_session_maker,
        is_default=True,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    older = await create_project(
        tenant_session_maker,
        name="Older",
        permalink="older",
        external_id="project-older",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = await create_project(
        tenant_session_maker,
        name="Newer",
        permalink="newer",
        external_id="project-newer",
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=RecordingProjectDeleteEnqueuer(),
    )

    await service.delete_project(
        ProjectDeleteAcceptanceRequest(
            project_external_id="project-main",
            delete_notes=False,
        )
    )

    async with tenant_session_maker() as session:
        promoted_older = await session.get(Project, older.id)
        promoted_newer = await session.get(Project, newer.id)

    assert promoted_older is not None
    assert promoted_older.is_default is True
    assert promoted_newer is not None
    assert not promoted_newer.is_default


@pytest.mark.asyncio
async def test_project_delete_acceptance_reactivates_project_when_enqueue_fails(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    project = await create_project(tenant_session_maker)
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=FailingProjectDeleteEnqueuer(),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await service.delete_project(
            ProjectDeleteAcceptanceRequest(
                project_external_id="project-main",
                delete_notes=True,
            )
        )

    async with tenant_session_maker() as session:
        stored_project = await session.get(Project, project.id)

    assert stored_project is not None
    assert stored_project.is_active is True
