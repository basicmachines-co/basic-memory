from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from basic_memory.gateway.project_deletes import (
    ProjectDeleteAcceptanceError,
    ProjectDeleteAcceptanceRequest,
    ProjectDeleteAcceptanceService,
)
from basic_memory.models import Base as BasicMemoryBase
from basic_memory.models import Project
from basic_memory.runtime import RuntimeJobId, RuntimeProjectDeleteJobRequest
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
    is_default: bool = False,
) -> Project:
    async with session_maker() as session:
        project = Project(
            name="Main",
            path="basic-memory",
            permalink="main",
            external_id="project-main",
            is_active=True,
            is_default=is_default,
        )
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
    tenant_id = uuid4()
    project = await create_project(tenant_session_maker)
    enqueuer = RecordingProjectDeleteEnqueuer()
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=enqueuer,
    )

    result = await service.delete_project(
        ProjectDeleteAcceptanceRequest(
            tenant_id=tenant_id,
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
            tenant_id=tenant_id,
            project_id=project.id,
            project_external_id="project-main",
            project_name="Main",
            project_path="basic-memory",
            delete_notes=True,
        )
    ]
    assert result.to_response_payload()["job_id"] == "123"
    assert result.to_response_payload()["file_delete_status"] == "pending"
    assert result.old_project.external_id == "project-main"


@pytest.mark.asyncio
async def test_project_delete_acceptance_rejects_default_project_before_soft_delete(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    project = await create_project(tenant_session_maker, is_default=True)
    enqueuer = RecordingProjectDeleteEnqueuer()
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=enqueuer,
    )

    with pytest.raises(ProjectDeleteAcceptanceError) as exc_info:
        await service.delete_project(
            ProjectDeleteAcceptanceRequest(
                tenant_id=tenant_id,
                project_external_id="project-main",
                delete_notes=True,
            )
        )

    async with tenant_session_maker() as session:
        stored_project = await session.get(Project, project.id)

    assert exc_info.value.status_code == 400
    assert "Cannot delete default project" in exc_info.value.detail
    assert stored_project is not None
    assert stored_project.is_active is True
    assert enqueuer.requests == []


@pytest.mark.asyncio
async def test_project_delete_acceptance_reactivates_project_when_enqueue_fails(
    tenant_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    project = await create_project(tenant_session_maker)
    service = ProjectDeleteAcceptanceService(
        session_maker=tenant_session_maker,
        job_enqueuer=FailingProjectDeleteEnqueuer(),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await service.delete_project(
            ProjectDeleteAcceptanceRequest(
                tenant_id=tenant_id,
                project_external_id="project-main",
                delete_notes=True,
            )
        )

    async with tenant_session_maker() as session:
        stored_project = await session.get(Project, project.id)

    assert stored_project is not None
    assert stored_project.is_active is True
