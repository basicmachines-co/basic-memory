"""Tests for V2 project management API routes (ID-based endpoints)."""

import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from basic_memory import db
from basic_memory.config import ProjectEntry
from basic_memory.models import Project
from basic_memory.schemas.project_info import ProjectItem, ProjectStatusResponse
from basic_memory.schemas.v2 import ProjectResolveResponse


def _project_item(project: ProjectItem | None) -> ProjectItem:
    assert project is not None
    return project


async def _find_projects(project_repository, session_maker):
    async with db.scoped_session(session_maker) as session:
        return await project_repository.find_all(session)


async def _delete_project(project_repository, session_maker, project_id: int) -> bool:
    async with db.scoped_session(session_maker) as session:
        return await project_repository.delete(session, project_id)


async def _get_project_by_name(project_repository, session_maker, name: str):
    async with db.scoped_session(session_maker) as session:
        return await project_repository.get_by_name(session, name)


async def _get_default_project(project_repository, session_maker):
    async with db.scoped_session(session_maker) as session:
        return await project_repository.get_default_project(session)


async def _update_project(project_repository, session_maker, project_id: int, data: dict):
    async with db.scoped_session(session_maker) as session:
        return await project_repository.update(session, project_id, data)


@pytest.mark.asyncio
async def test_list_projects(client: AsyncClient, test_project: Project, v2_projects_url):
    """Test listing projects returns default_project from the database."""
    response = await client.get(f"{v2_projects_url}/")

    assert response.status_code == 200
    data = response.json()

    # default_project must be populated from the is_default flag in the database
    assert data["default_project"] == test_project.name

    project_names = [p["name"] for p in data["projects"]]
    assert test_project.name in project_names


@pytest.mark.asyncio
async def test_get_project_by_id(client: AsyncClient, test_project: Project, v2_projects_url):
    """Test getting a project by its external_id UUID."""
    response = await client.get(f"{v2_projects_url}/{test_project.external_id}")

    assert response.status_code == 200
    project = ProjectItem.model_validate(response.json())
    assert project.external_id == test_project.external_id
    assert project.name == test_project.name
    assert project.path == test_project.path
    assert project.is_default == (test_project.is_default or False)


@pytest.mark.asyncio
async def test_get_project_by_id_not_found(client: AsyncClient, v2_projects_url):
    """Test getting a non-existent project by external_id returns 404."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"{v2_projects_url}/{fake_uuid}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_add_project_response_reflects_promoted_default(
    client: AsyncClient,
    v2_projects_url,
    app_config,
    config_manager,
    config_home,
    project_repository,
    session_maker,
):
    """Regression #974/#985: POST response should echo persisted default promotion."""
    main_home = config_home / "basic-memory"
    main_home.mkdir(parents=True, exist_ok=True)
    qa_path = config_home / "qa-notes"
    qa_path.mkdir(parents=True, exist_ok=True)

    fresh_config = app_config.model_copy(
        update={
            "projects": {"main": ProjectEntry(path=str(main_home))},
            "default_project": "main",
        }
    )
    config_manager.save_config(fresh_config)

    for project in await _find_projects(project_repository, session_maker):
        await _delete_project(project_repository, session_maker, project.id)

    response = await client.post(
        f"{v2_projects_url}/",
        json={"name": "qa", "path": str(qa_path), "set_default": False},
    )

    assert response.status_code == 201
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
    assert status_response.default is True
    new_project = _project_item(status_response.new_project)
    assert new_project.name == "qa"
    assert new_project.is_default is True


@pytest.mark.asyncio
async def test_update_project_path_by_id(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test updating a project's path by external_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        new_path = str(Path(tmpdir) / "new-project-location")
        Path(new_path).mkdir(parents=True, exist_ok=True)

        update_data = {"path": new_path}
        response = await client.patch(
            f"{v2_projects_url}/{test_project.external_id}",
            json=update_data,
        )

        assert response.status_code == 200
        status_response = ProjectStatusResponse.model_validate(response.json())
        assert status_response.status == "success"
        new_project = _project_item(status_response.new_project)
        old_project = _project_item(status_response.old_project)
        assert new_project.external_id == test_project.external_id
        # Normalize paths for cross-platform comparison (Windows uses backslashes, API returns forward slashes)
        assert Path(new_project.path) == Path(new_path)
        assert old_project.external_id == test_project.external_id


@pytest.mark.asyncio
async def test_update_project_invalid_path(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test updating with a relative path returns 400."""
    update_data = {"path": "relative/path"}
    response = await client.patch(
        f"{v2_projects_url}/{test_project.external_id}",
        json=update_data,
    )

    assert response.status_code == 400
    assert "absolute" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_project_not_found(client: AsyncClient, v2_projects_url, tmp_path):
    """Test updating a non-existent project returns 404."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    # Use tmp_path for cross-platform absolute path compatibility
    new_path = str(tmp_path / "new-path")
    update_data = {"path": new_path}
    response = await client.patch(
        f"{v2_projects_url}/{fake_uuid}",
        json=update_data,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_set_default_project_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
    project_repository,
    project_service,
    session_maker,
):
    """Test setting a project as default by external_id."""
    # Create a second project to test setting default
    await project_service.add_project("second-project", "/tmp/second-project")

    # Get the created project from the repository to get its external_id
    created_project = await _get_project_by_name(
        project_repository, session_maker, "second-project"
    )
    assert created_project is not None

    # Set the second project as default
    response = await client.put(f"{v2_projects_url}/{created_project.external_id}/default")

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
    assert status_response.default is True
    new_project = _project_item(status_response.new_project)
    old_project = _project_item(status_response.old_project)
    assert new_project.external_id == created_project.external_id
    assert new_project.is_default is True
    assert old_project.external_id == test_project.external_id
    assert old_project.is_default is False


@pytest.mark.asyncio
async def test_set_default_project_when_none_is_set(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository, session_maker
):
    """Regression for #975: setting a default must succeed when none is set.

    This is the bootstrap/recovery case: `bm project default <name>` is exactly
    the command reached for when no default exists, so the endpoint must not 404.
    """
    # Clear any existing default so no row has is_default set.
    await _update_project(project_repository, session_maker, test_project.id, {"is_default": None})
    assert await _get_default_project(project_repository, session_maker) is None

    response = await client.put(f"{v2_projects_url}/{test_project.external_id}/default")

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
    assert status_response.default is True
    # No previous default existed, so old_project must be None.
    assert status_response.old_project is None
    new_project = _project_item(status_response.new_project)
    assert new_project.external_id == test_project.external_id
    assert new_project.is_default is True

    # A follow-up read-back must now return the newly set default.
    default_project = await _get_default_project(project_repository, session_maker)
    assert default_project is not None
    assert default_project.external_id == test_project.external_id


@pytest.mark.asyncio
async def test_set_default_project_not_found(client: AsyncClient, v2_projects_url):
    """Test setting a non-existent project as default returns 404."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.put(f"{v2_projects_url}/{fake_uuid}/default")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
    project_repository,
    project_service,
    session_maker,
):
    """Test deleting a project by external_id."""
    # Create a second project since we can't delete the default
    await project_service.add_project("to-delete", "/tmp/to-delete")

    # Get the created project from the repository to get its external_id
    created_project = await _get_project_by_name(project_repository, session_maker, "to-delete")
    assert created_project is not None

    # Delete it
    response = await client.delete(f"{v2_projects_url}/{created_project.external_id}")

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"
    old_project = _project_item(status_response.old_project)
    assert old_project.external_id == created_project.external_id
    assert status_response.new_project is None

    # Verify it's deleted - trying to get it should return 404
    response = await client.get(f"{v2_projects_url}/{created_project.external_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_with_delete_notes_param(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
    project_repository,
    project_service,
    session_maker,
):
    """Test deleting a project with delete_notes parameter."""
    # Create a project in a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "test-delete-notes"
        project_path.mkdir(parents=True, exist_ok=True)

        # Create a test file in the project
        test_file = project_path / "test.md"
        test_file.write_text("Test content")

        await project_service.add_project("delete-with-notes", str(project_path))

        # Get the created project from the repository to get its external_id
        created_project = await _get_project_by_name(
            project_repository, session_maker, "delete-with-notes"
        )
        assert created_project is not None

        # Delete with delete_notes=true
        response = await client.delete(
            f"{v2_projects_url}/{created_project.external_id}?delete_notes=true"
        )

        assert response.status_code == 200

        # Verify directory was deleted
        assert not project_path.exists()


@pytest.mark.asyncio
async def test_delete_default_project_fails(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test that deleting the default project returns 400."""
    # test_project is the default project
    response = await client.delete(f"{v2_projects_url}/{test_project.external_id}")

    assert response.status_code == 400
    assert "default project" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_project_not_found(client: AsyncClient, v2_projects_url):
    """Test deleting a non-existent project returns 404."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.delete(f"{v2_projects_url}/{fake_uuid}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_v2_project_endpoints_use_id_not_name(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Verify v2 project endpoints require project external_id UUID, not name."""
    # Try using project name instead of external_id - should fail
    response = await client.get(f"{v2_projects_url}/{test_project.name}")

    # Should get 404 because name is not a valid project external_id
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_project_index_uses_event_indexer_not_sync_service(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
):
    """Foreground project index should return project-index fanout counts."""
    note_path = Path(test_project.path) / "incoming" / "project-index.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Project Index\n\nIndexed by project fanout.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_projects_url}/{test_project.external_id}/index",
        params={"run_in_background": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 1
    assert data["enqueued_files"] == 1
    assert data["enqueued_batches"] == 1
    assert data["deleted_files"] == 0
    assert "new" not in data


@pytest.mark.asyncio
async def test_project_index_foreground_response_payload_snapshot(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
):
    """The typed response union must keep the foreground payload byte-identical."""
    note_path = Path(test_project.path) / "incoming" / "index-snapshot.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Index Snapshot\n\nOne indexable file.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_projects_url}/{test_project.external_id}/index",
        params={"run_in_background": False},
    )

    assert response.status_code == 200
    assert response.content == (
        b'{"total_files":1,"enqueued_files":1,"enqueued_batches":1,"deleted_files":0}'
    )


@pytest.mark.asyncio
async def test_project_index_background_response_payload_snapshot(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
):
    """The typed response union must keep the background payload byte-identical."""
    response = await client.post(f"{v2_projects_url}/{test_project.external_id}/index")

    assert response.status_code == 200
    expected_message = f"Filesystem indexing initiated for project '{test_project.name}'"
    assert response.content == (
        f'{{"status":"index_started","message":"{expected_message}"}}'.encode()
    )


@pytest.mark.asyncio
async def test_project_status_uses_event_index_report_not_sync_service(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
):
    """Project status should observe current project-index files without SyncService."""
    note_path = Path(test_project.path) / "incoming" / "project-status.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_content = "# Project Status\n\nVisible in status report.\n"
    note_path.write_text(note_content, encoding="utf-8")

    response = await client.post(f"{v2_projects_url}/{test_project.external_id}/status")

    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 1
    assert data["observed_files"] == [
        {
            "path": "incoming/project-status.md",
            "checksum": data["observed_files"][0]["checksum"],
            "size": note_path.stat().st_size,
        }
    ]
    assert "new" not in data


@pytest.mark.asyncio
async def test_project_id_stability_after_rename(
    client: AsyncClient, test_project: Project, v2_projects_url, project_repository
):
    """Test that project external_id remains stable even after renaming."""
    original_external_id = test_project.external_id
    original_name = test_project.name

    # Get project by external_id
    response = await client.get(f"{v2_projects_url}/{original_external_id}")
    assert response.status_code == 200
    project_before = ProjectItem.model_validate(response.json())
    assert project_before.external_id == original_external_id
    assert project_before.name == original_name

    # Even if we renamed the project (not testing rename here, just the concept),
    # the external_id would stay the same. This test demonstrates the stability.
    # Re-fetch by same external_id
    response = await client.get(f"{v2_projects_url}/{original_external_id}")
    assert response.status_code == 200
    project_after = ProjectItem.model_validate(response.json())
    assert project_after.external_id == original_external_id


@pytest.mark.asyncio
async def test_update_project_active_status(
    client: AsyncClient,
    test_project: Project,
    v2_projects_url,
    project_repository,
    project_service,
    session_maker,
):
    """Test updating a project's active status by external_id."""
    # Create a non-default project
    await project_service.add_project("test-active", "/tmp/test-active")

    # Get the created project from the repository to get its external_id
    created_project = await _get_project_by_name(project_repository, session_maker, "test-active")
    assert created_project is not None

    # Update active status
    update_data = {"is_active": False}
    response = await client.patch(
        f"{v2_projects_url}/{created_project.external_id}",
        json=update_data,
    )

    assert response.status_code == 200
    status_response = ProjectStatusResponse.model_validate(response.json())
    assert status_response.status == "success"


@pytest.mark.asyncio
async def test_resolve_project_by_name(client: AsyncClient, test_project: Project, v2_projects_url):
    """Test resolving a project by name returns correct project external_id."""
    resolve_data = {"identifier": test_project.name}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 200
    resolved = ProjectResolveResponse.model_validate(response.json())
    assert resolved.external_id == test_project.external_id
    assert resolved.name == test_project.name
    assert resolved.path == test_project.path
    assert resolved.is_default == (test_project.is_default or False)
    # Resolution method could be "name" or "permalink" depending on whether name == permalink
    assert resolved.resolution_method in ["name", "permalink"]


@pytest.mark.asyncio
async def test_resolve_project_by_permalink(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test resolving a project by permalink returns correct project external_id."""
    # Assume test_project.name can be converted to permalink
    from basic_memory.utils import generate_permalink

    project_permalink = generate_permalink(test_project.name)
    resolve_data = {"identifier": project_permalink}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 200
    resolved = ProjectResolveResponse.model_validate(response.json())
    assert resolved.external_id == test_project.external_id
    assert resolved.name == test_project.name
    # Resolution method could be "name" or "permalink" depending on implementation
    assert resolved.resolution_method in ["name", "permalink"]


@pytest.mark.asyncio
async def test_resolve_project_by_workspace_qualified_permalink(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Resolve the workspace/project form shown by MCP disambiguation errors."""
    resolve_data = {"identifier": f"personal/{test_project.name}"}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 200
    resolved = ProjectResolveResponse.model_validate(response.json())
    assert resolved.external_id == test_project.external_id
    assert resolved.name == test_project.name
    assert resolved.resolution_method == "permalink"


@pytest.mark.asyncio
async def test_resolve_project_by_id(client: AsyncClient, test_project: Project, v2_projects_url):
    """Test resolving a project by external_id string returns correct project external_id."""
    resolve_data = {"identifier": test_project.external_id}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 200
    resolved = ProjectResolveResponse.model_validate(response.json())
    assert resolved.external_id == test_project.external_id
    assert resolved.name == test_project.name
    assert resolved.resolution_method == "external_id"


@pytest.mark.asyncio
async def test_resolve_project_case_insensitive(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """Test resolving a project by name is case-insensitive."""
    resolve_data = {"identifier": test_project.name.upper()}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 200
    resolved = ProjectResolveResponse.model_validate(response.json())
    assert resolved.external_id == test_project.external_id
    assert resolved.name == test_project.name


@pytest.mark.asyncio
async def test_resolve_project_not_found(client: AsyncClient, v2_projects_url):
    """Test resolving a non-existent project returns 404."""
    resolve_data = {"identifier": "nonexistent-project"}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_resolve_project_not_found_fresh_install_names_setup_command(
    client: AsyncClient, v2_projects_url, project_repository, session_maker
):
    """#974 follow-up: a fresh install fails its first read with a bare not-found.

    config.json bootstraps a "main" default before any reconciliation has created
    database rows (the one-shot CLI never runs the server lifespan), so resolving
    the configured default 404s. With an empty projects table the error must point
    at first-run setup instead of reading like a broken install.
    """
    for project in await _find_projects(project_repository, session_maker):
        await _delete_project(project_repository, session_maker, project.id)

    response = await client.post(f"{v2_projects_url}/resolve", json={"identifier": "main"})

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail.startswith("Project not found: 'main'")
    assert "basic-memory project add" in detail


@pytest.mark.asyncio
async def test_resolve_project_not_found_with_projects_keeps_plain_message(
    client: AsyncClient, test_project: Project, v2_projects_url
):
    """A miss against a populated projects table stays a plain not-found."""
    response = await client.post(
        f"{v2_projects_url}/resolve", json={"identifier": "nonexistent-project"}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found: 'nonexistent-project'"


@pytest.mark.asyncio
async def test_resolve_project_empty_identifier(client: AsyncClient, v2_projects_url):
    """Test resolving with empty identifier returns 422."""
    resolve_data = {"identifier": ""}
    response = await client.post(f"{v2_projects_url}/resolve", json=resolve_data)

    assert response.status_code == 422  # Validation error
