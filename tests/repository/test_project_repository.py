"""Tests for the ProjectRepository."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from basic_memory import db
from basic_memory.models.project import Project
from basic_memory.repository.project_repository import ProjectRepository


@pytest_asyncio.fixture
async def sample_project(project_repository: ProjectRepository) -> Project:
    """Create a sample project for testing."""
    project_data = {
        "name": "Sample Project",
        "description": "A sample project",
        "path": "/sample/project/path",
        "is_active": True,
        "is_default": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return await project_repository.create(project_data)


@pytest.mark.asyncio
async def test_create_project(project_repository: ProjectRepository):
    """Test creating a new project."""
    project_data = {
        "name": "Sample Project",
        "description": "A sample project",
        "path": "/sample/project/path",
        "is_active": True,
        "is_default": False,
    }
    project = await project_repository.create(project_data)

    # Verify returned object
    assert project.id is not None
    assert project.name == "Sample Project"
    assert project.description == "A sample project"
    assert project.path == "/sample/project/path"
    assert project.is_active is True
    assert project.is_default is False
    assert isinstance(project.created_at, datetime)
    assert isinstance(project.updated_at, datetime)

    # Verify permalink was generated correctly
    assert project.permalink == "sample-project"

    # Verify in database
    found = await project_repository.find_by_id(project.id)
    assert found is not None
    assert found.id == project.id
    assert found.name == project.name
    assert found.description == project.description
    assert found.path == project.path
    assert found.permalink == "sample-project"
    assert found.is_active is True
    assert found.is_default is False


@pytest.mark.asyncio
async def test_get_by_name(project_repository: ProjectRepository, sample_project: Project):
    """Test getting a project by name."""
    # Test exact match
    found = await project_repository.get_by_name(sample_project.name)
    assert found is not None
    assert found.id == sample_project.id
    assert found.name == sample_project.name

    # Test non-existent name
    found = await project_repository.get_by_name("Non-existent Project")
    assert found is None


@pytest.mark.asyncio
async def test_get_by_permalink(project_repository: ProjectRepository, sample_project: Project):
    """Test getting a project by permalink."""
    # Verify the permalink value
    assert sample_project.permalink == "sample-project"

    # Test exact match
    found = await project_repository.get_by_permalink(sample_project.permalink)
    assert found is not None
    assert found.id == sample_project.id
    assert found.permalink == sample_project.permalink

    # Test non-existent permalink
    found = await project_repository.get_by_permalink("non-existent-project")
    assert found is None


@pytest.mark.asyncio
async def test_get_by_path(project_repository: ProjectRepository, sample_project: Project):
    """Test getting a project by path."""
    # Test exact match
    found = await project_repository.get_by_path(sample_project.path)
    assert found is not None
    assert found.id == sample_project.id
    assert found.path == sample_project.path

    # Test with Path object
    found = await project_repository.get_by_path(Path(sample_project.path))
    assert found is not None
    assert found.id == sample_project.id
    assert found.path == sample_project.path

    # Test non-existent path
    found = await project_repository.get_by_path("/non/existent/path")
    assert found is None


@pytest.mark.asyncio
async def test_get_default_project(project_repository: ProjectRepository):
    """Test getting the default project."""
    # We already have a default project from the test_project fixture
    # So just create a non-default project
    non_default_project_data = {
        "name": "Non-Default Project",
        "description": "A non-default project",
        "path": "/non-default/project/path",
        "is_active": True,
        "is_default": None,  # Not the default project
    }

    await project_repository.create(non_default_project_data)

    # Get default project
    default_project = await project_repository.get_default_project()
    assert default_project is not None
    assert default_project.is_default is True


@pytest.mark.asyncio
async def test_get_active_projects(project_repository: ProjectRepository):
    """Test getting all active projects."""
    # Create active and inactive projects
    active_project_data = {
        "name": "Active Project",
        "description": "An active project",
        "path": "/active/project/path",
        "is_active": True,
    }
    inactive_project_data = {
        "name": "Inactive Project",
        "description": "An inactive project",
        "path": "/inactive/project/path",
        "is_active": False,
    }

    await project_repository.create(active_project_data)
    await project_repository.create(inactive_project_data)

    # Get active projects
    active_projects = await project_repository.get_active_projects()
    assert len(active_projects) >= 1  # Could be more from other tests

    # Verify that all returned projects are active
    for project in active_projects:
        assert project.is_active is True

    # Verify active project is included
    active_names = [p.name for p in active_projects]
    assert "Active Project" in active_names

    # Verify inactive project is not included
    assert "Inactive Project" not in active_names


@pytest.mark.asyncio
async def test_set_as_default(project_repository: ProjectRepository, test_project: Project):
    """Test setting a project as default."""
    # The test_project fixture is already the default
    # Create a non-default project
    project2_data = {
        "name": "Project 2",
        "description": "Project 2",
        "path": "/project2/path",
        "is_active": True,
        "is_default": None,  # Not default
    }

    # Get the existing default project
    project1 = test_project
    project2 = await project_repository.create(project2_data)

    # Verify initial state
    assert project1.is_default is True
    assert project2.is_default is None

    # Set project2 as default
    updated_project2 = await project_repository.set_as_default(project2.id)
    assert updated_project2 is not None
    assert updated_project2.is_default is True

    # Verify project1 is no longer default
    project1_updated = await project_repository.find_by_id(project1.id)
    assert project1_updated is not None
    assert project1_updated.is_default is None

    # Verify project2 is now default
    project2_updated = await project_repository.find_by_id(project2.id)
    assert project2_updated is not None
    assert project2_updated.is_default is True


@pytest.mark.asyncio
async def test_update_project(project_repository: ProjectRepository, sample_project: Project):
    """Test updating a project."""
    # Update project
    updated_data = {
        "name": "Updated Project Name",
        "description": "Updated description",
        "path": "/updated/path",
    }
    updated_project = await project_repository.update(sample_project.id, updated_data)

    # Verify returned object
    assert updated_project is not None
    assert updated_project.id == sample_project.id
    assert updated_project.name == "Updated Project Name"
    assert updated_project.description == "Updated description"
    assert updated_project.path == "/updated/path"

    # Verify permalink was updated based on new name
    assert updated_project.permalink == "updated-project-name"

    # Verify in database
    found = await project_repository.find_by_id(sample_project.id)
    assert found is not None
    assert found.name == "Updated Project Name"
    assert found.description == "Updated description"
    assert found.path == "/updated/path"
    assert found.permalink == "updated-project-name"

    # Verify we can find by the new permalink
    found_by_permalink = await project_repository.get_by_permalink("updated-project-name")
    assert found_by_permalink is not None
    assert found_by_permalink.id == sample_project.id


@pytest.mark.asyncio
async def test_delete_project(project_repository: ProjectRepository, sample_project: Project):
    """Test deleting a project."""
    # Delete project
    result = await project_repository.delete(sample_project.id)
    assert result is True

    # Verify deletion
    deleted = await project_repository.find_by_id(sample_project.id)
    assert deleted is None

    # Verify with direct database query
    async with db.scoped_session(project_repository.session_maker) as session:
        query = select(Project).filter(Project.id == sample_project.id)
        result = await session.execute(query)
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_nonexistent_project(project_repository: ProjectRepository):
    """Test deleting a project that doesn't exist."""
    result = await project_repository.delete(999)  # Non-existent ID
    assert result is False
