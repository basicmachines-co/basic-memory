"""GET /v2/projects/{id}/doctor reports a project's problems without changing it."""

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas.doctor import (
    DoctorCheck,
    DoctorSeverity,
    IndexProjectAction,
    ProjectDoctorReport,
)


@pytest.mark.asyncio
async def test_never_indexed_project_with_files_suggests_indexing(
    client: AsyncClient, test_project: Project, v2_projects_url: str
):
    note = Path(test_project.path) / "notes" / "first.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# First\n", encoding="utf-8")

    response = await client.get(f"{v2_projects_url}/{test_project.external_id}/doctor")

    assert response.status_code == 200
    report = ProjectDoctorReport.model_validate(response.json())
    (finding,) = report.findings
    assert finding.check is DoctorCheck.INDEX_READINESS
    assert finding.severity is DoctorSeverity.WARNING
    assert finding.action == IndexProjectAction()
    assert response.json()["has_errors"] is False


@pytest.mark.asyncio
async def test_orphaned_search_row_is_reported_without_being_repaired(
    client: AsyncClient, test_project: Project, v2_projects_url: str, session_maker
):
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_index (project_id, id, entity_id, type, title) "
                "VALUES (:project_id, 999, 999, 'entity', 'ghost')"
            ),
            {"project_id": test_project.id},
        )
        await session.commit()

    url = f"{v2_projects_url}/{test_project.external_id}/doctor"
    first = ProjectDoctorReport.model_validate((await client.get(url)).json())
    second = ProjectDoctorReport.model_validate((await client.get(url)).json())

    orphan_findings = [
        finding for finding in first.findings if finding.check is DoctorCheck.SEARCH_INDEX
    ]
    assert [finding.evidence for finding in orphan_findings] == [("entity id=999, entity_id=999",)]
    # Reading twice gives the same report: doctor never repairs what it reports.
    assert second == first


@pytest.mark.asyncio
async def test_missing_project_directory_reports_only_the_path(
    client: AsyncClient, v2_projects_url: str, session_maker, tmp_path: Path
):
    missing = tmp_path / "moved-away"
    async with db.scoped_session(session_maker) as session:
        project = await ProjectRepository().create(
            session,
            {"name": "moved", "path": str(missing), "is_active": True, "is_default": False},
        )

    response = await client.get(f"{v2_projects_url}/{project.external_id}/doctor")

    assert response.status_code == 200
    report = ProjectDoctorReport.model_validate(response.json())
    (finding,) = report.findings
    assert finding.check is DoctorCheck.PROJECT_PATH
    assert finding.evidence == (str(missing),)
    assert report.has_errors


@pytest.mark.asyncio
async def test_unknown_project_is_not_found(client: AsyncClient, v2_projects_url: str):
    response = await client.get(f"{v2_projects_url}/00000000-0000-0000-0000-000000000000/doctor")
    assert response.status_code == 404
