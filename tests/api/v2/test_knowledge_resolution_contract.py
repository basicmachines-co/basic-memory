"""Focused API regressions for strict entity versus cross-project link resolution."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas.v2 import EntityResolveResponse


@pytest.mark.asyncio
async def test_strict_entity_resolution_rejects_legacy_cross_project_path_after_local_miss(
    client: AsyncClient,
    session_maker,
    test_project: Project,
    tmp_path,
    v2_project_url: str,
) -> None:
    """A project/path reference stays local when present and migrates when absent."""
    now = datetime.now(UTC)
    async with db.scoped_session(session_maker) as session:
        await ProjectRepository().create(
            session,
            {
                "name": "other-project",
                "description": "Secondary project",
                "path": str(tmp_path / "other-project"),
                "is_active": True,
                "is_default": False,
            },
        )
        local_entity = await EntityRepository(project_id=test_project.id).add(
            session,
            Entity(
                title="Local note",
                note_type="note",
                content_type="text/markdown",
                file_path="other-project/docs/local-note.md",
                permalink="other-project/docs/local-note",
                created_at=now,
                updated_at=now,
                project_id=test_project.id,
            ),
        )
        namespaced_title_entity = await EntityRepository(project_id=test_project.id).add(
            session,
            Entity(
                title="C++::ABI",
                note_type="note",
                content_type="text/markdown",
                file_path="docs/cxx-abi.md",
                permalink="docs/cxx-abi",
                created_at=now,
                updated_at=now,
                project_id=test_project.id,
            ),
        )

    local_response = await client.post(
        f"{v2_project_url}/knowledge/resolve",
        json={"identifier": "other-project/docs/local-note", "strict": True},
    )
    qualified_miss_response = await client.post(
        f"{v2_project_url}/knowledge/resolve",
        json={"identifier": "other-project/docs/missing", "strict": True},
    )
    namespaced_title_response = await client.post(
        f"{v2_project_url}/knowledge/resolve",
        json={"identifier": "C++::ABI", "strict": True},
    )

    assert local_response.status_code == 200
    assert EntityResolveResponse.model_validate(local_response.json()).entity_id == local_entity.id
    assert namespaced_title_response.status_code == 200
    assert (
        EntityResolveResponse.model_validate(namespaced_title_response.json()).entity_id
        == namespaced_title_entity.id
    )
    assert qualified_miss_response.status_code == 400
    assert "/knowledge/links/resolve" in qualified_miss_response.json()["detail"]
