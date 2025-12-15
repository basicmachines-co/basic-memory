"""Tests for ProjectService."""

import os
import tempfile
from pathlib import Path

import pytest

from basic_memory.schemas import (
    ProjectInfoResponse,
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
)
from basic_memory.services.project_service import ProjectService
from basic_memory.config import ConfigManager


def test_projects_property(project_service: ProjectService):
    """Test the projects property."""
    # Get the projects
    projects = project_service.projects

    # Assert that it returns a dictionary
    assert isinstance(projects, dict)
    # The test config should have at least one project
    assert len(projects) > 0


def test_default_project_property(project_service: ProjectService):
    """Test the default_project property."""
    # Get the default project
    default_project = project_service.default_project

    # Assert it's a string and has a value
    assert isinstance(default_project, str)
    assert default_project


def test_current_project_property(project_service: ProjectService):
    """Test the current_project property."""
    # Save original environment
    original_env = os.environ.get("BASIC_MEMORY_PROJECT")

    try:
        # Test with environment variable not set
        if "BASIC_MEMORY_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_PROJECT"]

        # Should return default_project when env var not set
        assert project_service.current_project == project_service.default_project

        # Now set the environment variable
        os.environ["BASIC_MEMORY_PROJECT"] = "test-project"

        # Should return env var value
        assert project_service.current_project == "test-project"
    finally:
        # Restore original environment
        if original_env is not None:
            os.environ["BASIC_MEMORY_PROJECT"] = original_env
        elif "BASIC_MEMORY_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_PROJECT"]

    """Test the methods of ProjectService."""


@pytest.mark.asyncio
async def test_project_operations_sync_methods(
    app_config, project_service: ProjectService, config_manager: ConfigManager
):
    """Test adding, switching, and removing a project using ConfigManager directly.

    This test uses the ConfigManager directly instead of the async methods.
    """
    # Generate a unique project name for testing
    test_project_name = f"test-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = test_root / "test-project"

        # Make sure the test directory exists
        test_project_path.mkdir(parents=True, exist_ok=True)

        try:
            # Test adding a project (using ConfigManager directly)
            config_manager.add_project(test_project_name, str(test_project_path))

            # Verify it was added
            assert test_project_name in project_service.projects
            assert Path(project_service.projects[test_project_name]) == test_project_path

            # Test setting as default
            original_default = project_service.default_project
            config_manager.set_default_project(test_project_name)
            assert project_service.default_project == test_project_name

            # Restore original default
            if original_default:
                config_manager.set_default_project(original_default)

            # Test removing the project
            config_manager.remove_project(test_project_name)
            assert test_project_name not in project_service.projects

        except Exception as e:
            # Clean up in case of error
            if test_project_name in project_service.projects:
                try:
                    config_manager.remove_project(test_project_name)
                except Exception:
                    pass
            raise e


@pytest.mark.asyncio
async def test_get_system_status(project_service: ProjectService):
    """Test getting system status."""
    # Get the system status
    status = project_service.get_system_status()

    # Assert it returns a valid SystemStatus object
    assert isinstance(status, SystemStatus)
    assert status.version
    assert status.database_path
    assert status.database_size


@pytest.mark.asyncio
async def test_get_statistics(project_service: ProjectService, test_graph, test_project):
    """Test getting statistics."""
    # Get statistics
    statistics = await project_service.get_statistics(test_project.id)

    # Assert it returns a valid ProjectStatistics object
    assert isinstance(statistics, ProjectStatistics)
    assert statistics.total_entities > 0
    assert "test" in statistics.entity_types


@pytest.mark.asyncio
async def test_get_activity_metrics(project_service: ProjectService, test_graph, test_project):
    """Test getting activity metrics."""
    # Get activity metrics
    metrics = await project_service.get_activity_metrics(test_project.id)

    # Assert it returns a valid ActivityMetrics object
    assert isinstance(metrics, ActivityMetrics)
    assert len(metrics.recently_created) > 0
    assert len(metrics.recently_updated) > 0


@pytest.mark.asyncio
async def test_get_project_info(project_service: ProjectService, test_graph, test_project):
    """Test getting full project info."""
    # Get project info
    info = await project_service.get_project_info(test_project.name)

    # Assert it returns a valid ProjectInfoResponse object
    assert isinstance(info, ProjectInfoResponse)
    assert info.project_name
    assert info.project_path
    assert info.default_project
    assert isinstance(info.available_projects, dict)
    assert isinstance(info.statistics, ProjectStatistics)
    assert isinstance(info.activity, ActivityMetrics)
    assert isinstance(info.system, SystemStatus)


@pytest.mark.asyncio
async def test_add_project_async(project_service: ProjectService):
    """Test adding a project with the updated async method."""
    test_project_name = f"test-async-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = test_root / "test-async-project"

        # Make sure the test directory exists
        test_project_path.mkdir(parents=True, exist_ok=True)

        try:
            # Test adding a project
            await project_service.add_project(test_project_name, str(test_project_path))

            # Verify it was added to config
            assert test_project_name in project_service.projects
            assert Path(project_service.projects[test_project_name]) == test_project_path

            # Verify it was added to the database
            project = await project_service.repository.get_by_name(test_project_name)
            assert project is not None
            assert project.name == test_project_name
            assert Path(project.path) == test_project_path

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)

            # Ensure it was removed from both config and DB
            assert test_project_name not in project_service.projects
            project = await project_service.repository.get_by_name(test_project_name)
            assert project is None


@pytest.mark.asyncio
async def test_set_default_project_async(project_service: ProjectService, test_project):
    """Test setting a project as default with the updated async method."""
    # First add a test project
    test_project_name = f"test-default-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-default-project")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        original_default = project_service.default_project

        try:
            # Add the test project
            await project_service.add_project(test_project_name, test_project_path)

            # Set as default
            await project_service.set_default_project(test_project_name)

            # Verify it's set as default in config
            assert project_service.default_project == test_project_name

            # Verify it's set as default in database
            project = await project_service.repository.get_by_name(test_project_name)
            assert project is not None
            assert project.is_default is True

            # Make sure old default is no longer default
            old_default_project = await project_service.repository.get_by_name(original_default)
            if old_default_project:
                assert old_default_project.is_default is not True

        finally:
            # Restore original default (only if it exists in database)
            if original_default:
                original_project = await project_service.repository.get_by_name(original_default)
                if original_project:
                    await project_service.set_default_project(original_default)

            # Clean up test project
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_get_project_method(project_service: ProjectService):
    """Test the get_project method directly."""
    test_project_name = f"test-get-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = (test_root / "test-get-project").as_posix()

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        try:
            # Test getting a non-existent project
            result = await project_service.get_project("non-existent-project")
            assert result is None

            # Add a project
            await project_service.add_project(test_project_name, test_project_path)

            # Test getting an existing project
            result = await project_service.get_project(test_project_name)
            assert result is not None
            assert result.name == test_project_name
            assert result.path == test_project_path

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_set_default_project_config_db_mismatch(
    project_service: ProjectService, config_manager: ConfigManager
):
    """Test set_default_project when project exists in config but not in database."""
    test_project_name = f"test-mismatch-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-mismatch-project")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        original_default = project_service.default_project

        try:
            # Add project to config only (not to database)
            config_manager.add_project(test_project_name, test_project_path)

            # Verify it's in config but not in database
            assert test_project_name in project_service.projects
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is None

            # Try to set as default - this should trigger the error log on line 142
            await project_service.set_default_project(test_project_name)

            # Should still update config despite database mismatch
            assert project_service.default_project == test_project_name

        finally:
            # Restore original default
            if original_default:
                config_manager.set_default_project(original_default)

            # Clean up
            if test_project_name in project_service.projects:
                config_manager.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_add_project_with_set_default_true(project_service: ProjectService, test_project):
    """Test adding a project with set_default=True enforces single default."""
    test_project_name = f"test-default-true-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-default-true")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        original_default = project_service.default_project

        try:
            # Get original default project from database
            original_default_project = await project_service.repository.get_by_name(
                original_default
            )

            # Add project with set_default=True
            await project_service.add_project(
                test_project_name, test_project_path, set_default=True
            )

            # Verify new project is set as default in both config and database
            assert project_service.default_project == test_project_name

            new_project = await project_service.repository.get_by_name(test_project_name)
            assert new_project is not None
            assert new_project.is_default is True

            # Verify original default is no longer default in database
            if original_default_project:
                refreshed_original = await project_service.repository.get_by_name(original_default)
                assert refreshed_original.is_default is not True

            # Verify only one project has is_default=True
            all_projects = await project_service.repository.find_all()
            default_projects = [p for p in all_projects if p.is_default is True]
            assert len(default_projects) == 1
            assert default_projects[0].name == test_project_name

        finally:
            # Restore original default (only if it exists in database)
            if original_default:
                original_project = await project_service.repository.get_by_name(original_default)
                if original_project:
                    await project_service.set_default_project(original_default)

            # Clean up test project
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_add_project_with_set_default_false(project_service: ProjectService):
    """Test adding a project with set_default=False doesn't change defaults."""
    test_project_name = f"test-default-false-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-default-false")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        original_default = project_service.default_project

        try:
            # Add project with set_default=False (explicit)
            await project_service.add_project(
                test_project_name, test_project_path, set_default=False
            )

            # Verify default project hasn't changed
            assert project_service.default_project == original_default

            # Verify new project is NOT set as default
            new_project = await project_service.repository.get_by_name(test_project_name)
            assert new_project is not None
            assert new_project.is_default is not True

            # Verify original default is still default
            original_default_project = await project_service.repository.get_by_name(
                original_default
            )
            if original_default_project:
                assert original_default_project.is_default is True

        finally:
            # Clean up test project
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_add_project_default_parameter_omitted(project_service: ProjectService):
    """Test adding a project without set_default parameter defaults to False behavior."""
    test_project_name = f"test-default-omitted-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-default-omitted")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        original_default = project_service.default_project

        try:
            # Add project without set_default parameter (should default to False)
            await project_service.add_project(test_project_name, test_project_path)

            # Verify default project hasn't changed
            assert project_service.default_project == original_default

            # Verify new project is NOT set as default
            new_project = await project_service.repository.get_by_name(test_project_name)
            assert new_project is not None
            assert new_project.is_default is not True

        finally:
            # Clean up test project
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_ensure_single_default_project_enforcement_logic(
    project_service: ProjectService, test_project
):
    """Test that _ensure_single_default_project logic works correctly."""
    # Test that the method exists and is callable
    assert hasattr(project_service, "_ensure_single_default_project")
    assert callable(getattr(project_service, "_ensure_single_default_project"))

    # Call the enforcement method - should work without error
    await project_service._ensure_single_default_project()

    # Verify there is exactly one default project after enforcement
    all_projects = await project_service.repository.find_all()
    default_projects = [p for p in all_projects if p.is_default is True]
    assert len(default_projects) == 1  # Should have exactly one default


@pytest.mark.asyncio
async def test_synchronize_projects_calls_ensure_single_default(project_service: ProjectService):
    """Test that synchronize_projects calls _ensure_single_default_project."""
    test_project_name = f"test-sync-default-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-sync-default")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        config_manager = ConfigManager()
        try:
            # Add project to config only (simulating unsynchronized state)
            config_manager.add_project(test_project_name, test_project_path)

            # Verify it's in config but not in database
            assert test_project_name in project_service.projects
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is None

            # Call synchronize_projects (this should call _ensure_single_default_project)
            await project_service.synchronize_projects()

            # Verify project is now in database
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is not None

            # Verify default project enforcement was applied
            all_projects = await project_service.repository.find_all()
            default_projects = [p for p in all_projects if p.is_default is True]
            assert len(default_projects) <= 1  # Should be exactly 1 or 0

        finally:
            # Clean up test project
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_synchronize_projects_normalizes_project_names(project_service: ProjectService):
    """Test that synchronize_projects normalizes project names in config to match database format."""
    # Use a project name that needs normalization (uppercase, spaces)
    unnormalized_name = "Test Project With Spaces"
    expected_normalized_name = "test-project-with-spaces"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-project-spaces")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        config_manager = ConfigManager()
        try:
            # Manually add the unnormalized project name to config

            # Add project with unnormalized name directly to config
            config = config_manager.load_config()
            config.projects[unnormalized_name] = test_project_path
            config_manager.save_config(config)

            # Verify the unnormalized name is in config
            assert unnormalized_name in project_service.projects
            assert project_service.projects[unnormalized_name] == test_project_path

            # Call synchronize_projects - this should normalize the project name
            await project_service.synchronize_projects()

            # Verify the config was updated with normalized name
            assert expected_normalized_name in project_service.projects
            assert unnormalized_name not in project_service.projects
            assert project_service.projects[expected_normalized_name] == test_project_path

            # Verify the project was added to database with normalized name
            db_project = await project_service.repository.get_by_name(expected_normalized_name)
            assert db_project is not None
            assert db_project.name == expected_normalized_name
            assert db_project.path == test_project_path
            assert db_project.permalink == expected_normalized_name

            # Verify the unnormalized name is not in database
            unnormalized_db_project = await project_service.repository.get_by_name(
                unnormalized_name
            )
            assert unnormalized_db_project is None

        finally:
            # Clean up - remove any test projects from both config and database
            current_projects = project_service.projects.copy()
            for name in [unnormalized_name, expected_normalized_name]:
                if name in current_projects:
                    try:
                        await project_service.remove_project(name)
                    except Exception:
                        # Try to clean up manually if remove_project fails
                        try:
                            config_manager.remove_project(name)
                        except Exception:
                            pass

                        # Remove from database
                        db_project = await project_service.repository.get_by_name(name)
                        if db_project:
                            await project_service.repository.delete(db_project.id)


@pytest.mark.asyncio
async def test_move_project(project_service: ProjectService):
    """Test moving a project to a new location."""
    test_project_name = f"test-move-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        old_path = test_root / "old-location"
        new_path = test_root / "new-location"

        # Create old directory
        old_path.mkdir(parents=True, exist_ok=True)

        try:
            # Add project with initial path
            await project_service.add_project(test_project_name, str(old_path))

            # Verify initial state
            assert test_project_name in project_service.projects
            assert Path(project_service.projects[test_project_name]) == old_path

            project = await project_service.repository.get_by_name(test_project_name)
            assert project is not None
            assert Path(project.path) == old_path

            # Move project to new location
            await project_service.move_project(test_project_name, str(new_path))

            # Verify config was updated
            assert Path(project_service.projects[test_project_name]) == new_path

            # Verify database was updated
            updated_project = await project_service.repository.get_by_name(test_project_name)
            assert updated_project is not None
            assert Path(updated_project.path) == new_path

            # Verify new directory was created
            assert os.path.exists(new_path)

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_move_project_nonexistent(project_service: ProjectService):
    """Test moving a project that doesn't exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        new_path = str(test_root / "new-location")

        with pytest.raises(ValueError, match="not found in configuration"):
            await project_service.move_project("nonexistent-project", new_path)


@pytest.mark.asyncio
async def test_move_project_db_mismatch(project_service: ProjectService):
    """Test moving a project that exists in config but not in database."""
    test_project_name = f"test-move-mismatch-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        old_path = test_root / "old-location"
        new_path = test_root / "new-location"

        # Create directories
        old_path.mkdir(parents=True, exist_ok=True)

        config_manager = project_service.config_manager

        try:
            # Add project to config only (not to database)
            config_manager.add_project(test_project_name, str(old_path))

            # Verify it's in config but not in database
            assert test_project_name in project_service.projects
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is None

            # Try to move project - should fail and restore config
            with pytest.raises(ValueError, match="not found in database"):
                await project_service.move_project(test_project_name, str(new_path))

            # Verify config was restored to original path
            assert Path(project_service.projects[test_project_name]) == old_path

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                config_manager.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_move_project_expands_path(project_service: ProjectService):
    """Test that move_project expands ~ and relative paths."""
    test_project_name = f"test-move-expand-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        old_path = (test_root / "old-location").as_posix()

        # Create old directory
        os.makedirs(old_path, exist_ok=True)

        try:
            # Add project with initial path
            await project_service.add_project(test_project_name, old_path)

            # Use a relative path for the move
            relative_new_path = "./new-location"
            expected_absolute_path = Path(os.path.abspath(relative_new_path)).as_posix()

            # Move project using relative path
            await project_service.move_project(test_project_name, relative_new_path)

            # Verify the path was expanded to absolute
            assert project_service.projects[test_project_name] == expected_absolute_path

            updated_project = await project_service.repository.get_by_name(test_project_name)
            assert updated_project is not None
            assert updated_project.path == expected_absolute_path

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_synchronize_projects_handles_case_sensitivity_bug(project_service: ProjectService):
    """Test that synchronize_projects fixes the case sensitivity bug (Personal vs personal)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Simulate the exact bug scenario: config has "Personal" but database expects "personal"
        config_name = "Personal"
        normalized_name = "personal"
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "personal-project")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        config_manager = ConfigManager()
        try:
            # Add project with uppercase name to config (simulating the bug scenario)
            config = config_manager.load_config()
            config.projects[config_name] = test_project_path
            config_manager.save_config(config)

            # Verify the uppercase name is in config
            assert config_name in project_service.projects
            assert project_service.projects[config_name] == test_project_path

            # Call synchronize_projects - this should fix the case sensitivity issue
            await project_service.synchronize_projects()

            # Verify the config was updated to use normalized case
            assert normalized_name in project_service.projects
            assert config_name not in project_service.projects
            assert project_service.projects[normalized_name] == test_project_path

            # Verify the project exists in database with correct normalized name
            db_project = await project_service.repository.get_by_name(normalized_name)
            assert db_project is not None
            assert db_project.name == normalized_name
            assert db_project.path == test_project_path

            # Verify we can now switch to this project without case sensitivity errors
            # (This would have failed before the fix with "Personal" != "personal")
            project_lookup = await project_service.get_project(normalized_name)
            assert project_lookup is not None
            assert project_lookup.name == normalized_name

        finally:
            # Clean up
            for name in [config_name, normalized_name]:
                if name in project_service.projects:
                    try:
                        await project_service.remove_project(name)
                    except Exception:
                        # Manual cleanup if needed
                        try:
                            config_manager.remove_project(name)
                        except Exception:
                            pass

                        db_project = await project_service.repository.get_by_name(name)
                        if db_project:
                            await project_service.repository.delete(db_project.id)


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_sanitizes_paths(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT uses sanitized project name, ignoring user path.

    When project_root is set (cloud mode), the system should:
    1. Ignore the user's provided path completely
    2. Use the sanitized project name as the directory name
    3. Create a flat structure: /app/data/test-bisync instead of /app/data/documents/test bisync

    This prevents the bisync auto-discovery bug where nested paths caused duplicate project creation.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        test_cases = [
            # (project_name, user_path, expected_sanitized_name)
            # User path is IGNORED - only project name matters
            ("test", "anything/path", "test"),
            (
                "Test BiSync",
                "~/Documents/Test BiSync",
                "test-bi-sync",
            ),  # BiSync -> bi-sync (dash preserved)
            ("My Project", "/tmp/whatever", "my-project"),
            ("UPPERCASE", "~", "uppercase"),
            ("With Spaces", "~/Documents/With Spaces", "with-spaces"),
        ]

        for i, (project_name, user_path, expected_sanitized) in enumerate(test_cases):
            test_project_name = f"{project_name}-{i}"  # Make unique
            expected_final_segment = f"{expected_sanitized}-{i}"

            try:
                # Add the project - user_path should be ignored
                await project_service.add_project(test_project_name, user_path)

                # Verify the path uses sanitized project name, not user path
                assert test_project_name in project_service.projects
                actual_path = project_service.projects[test_project_name]

                # The path should be under project_root (resolve both to handle macOS /private/var)
                assert (
                    Path(actual_path).resolve().is_relative_to(Path(project_root_path).resolve())
                ), f"Path {actual_path} should be under {project_root_path}"

                # Verify the final path segment is the sanitized project name
                path_parts = Path(actual_path).parts
                final_segment = path_parts[-1]
                assert final_segment == expected_final_segment, (
                    f"Expected path segment '{expected_final_segment}', got '{final_segment}'"
                )

                # Clean up
                await project_service.remove_project(test_project_name)

            except ValueError as e:
                pytest.fail(f"Unexpected ValueError for project {test_project_name}: {e}")


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_rejects_escape_attempts(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT rejects paths that try to escape the project root."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        # Create a directory outside project_root to verify it's not accessible
        outside_dir = Path(temp_dir) / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        # All of these should succeed by being sanitized to paths under project_root
        # The sanitization removes dangerous patterns, so they don't escape
        safe_after_sanitization = [
            "../../../etc/passwd",
            "../../.env",
            "../../../home/user/.ssh/id_rsa",
        ]

        for i, attack_path in enumerate(safe_after_sanitization):
            test_project_name = f"project-root-attack-test-{i}"

            try:
                # Add the project
                await project_service.add_project(test_project_name, attack_path)

                # Verify it was sanitized to be under project_root (resolve to handle macOS /private/var)
                actual_path = project_service.projects[test_project_name]
                assert (
                    Path(actual_path).resolve().is_relative_to(Path(project_root_path).resolve())
                ), f"Sanitized path {actual_path} should be under {project_root_path}"

                # Clean up
                await project_service.remove_project(test_project_name)

            except ValueError:
                # If it raises ValueError, that's also acceptable for security
                pass


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_without_project_root_allows_arbitrary_paths(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that without BASIC_MEMORY_PROJECT_ROOT set, arbitrary paths are allowed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Ensure project_root is not set
        if "BASIC_MEMORY_PROJECT_ROOT" in os.environ:
            monkeypatch.delenv("BASIC_MEMORY_PROJECT_ROOT")

        # Create a test directory
        test_dir = Path(temp_dir) / "arbitrary-location"
        test_dir.mkdir(parents=True, exist_ok=True)

        test_project_name = "no-project-root-test"

        try:
            # Without project_root, we should be able to use arbitrary absolute paths
            await project_service.add_project(test_project_name, str(test_dir))

            # Verify the path was accepted as-is
            assert test_project_name in project_service.projects
            actual_path = project_service.projects[test_project_name]
            assert actual_path == str(test_dir)

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                await project_service.remove_project(test_project_name)


@pytest.mark.skip(
    reason="Obsolete: project_root mode now uses sanitized project name, not user path. See test_add_project_with_project_root_sanitizes_paths instead."
)
@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_normalizes_case(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT normalizes paths to lowercase.

    NOTE: This test is obsolete. After fixing the bisync duplicate project bug,
    project_root mode now ignores the user's path and uses the sanitized project name instead.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        test_cases = [
            # (input_path, expected_normalized_path)
            ("Documents/my-project", str(project_root_path / "documents" / "my-project")),
            ("UPPERCASE/PATH", str(project_root_path / "uppercase" / "path")),
            ("MixedCase/Path", str(project_root_path / "mixedcase" / "path")),
            ("documents/Test-TWO", str(project_root_path / "documents" / "test-two")),
        ]

        for i, (input_path, expected_path) in enumerate(test_cases):
            test_project_name = f"case-normalize-test-{i}"

            try:
                # Add the project
                await project_service.add_project(test_project_name, input_path)

                # Verify the path was normalized to lowercase (resolve both to handle macOS /private/var)
                assert test_project_name in project_service.projects
                actual_path = project_service.projects[test_project_name]
                assert Path(actual_path).resolve() == Path(expected_path).resolve(), (
                    f"Expected path {expected_path} but got {actual_path} for input {input_path}"
                )

                # Clean up
                await project_service.remove_project(test_project_name)

            except ValueError as e:
                pytest.fail(f"Unexpected ValueError for input path {input_path}: {e}")


@pytest.mark.skip(
    reason="Obsolete: project_root mode now uses sanitized project name, not user path."
)
@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_detects_case_collisions(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT detects case-insensitive path collisions.

    NOTE: This test is obsolete. After fixing the bisync duplicate project bug,
    project_root mode now ignores the user's path and uses the sanitized project name instead.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        # First, create a project with lowercase path
        first_project = "documents-project"
        await project_service.add_project(first_project, "documents/basic-memory")

        # Verify it was created with normalized lowercase path (resolve to handle macOS /private/var)
        assert first_project in project_service.projects
        first_path = project_service.projects[first_project]
        assert (
            Path(first_path).resolve()
            == (project_root_path / "documents" / "basic-memory").resolve()
        )

        # Now try to create a project with the same path but different case
        # This should be normalized to the same lowercase path and not cause a collision
        # since both will be normalized to the same path
        second_project = "documents-project-2"
        try:
            # This should succeed because both get normalized to the same lowercase path
            await project_service.add_project(second_project, "documents/basic-memory")
            # If we get here, both should have the exact same path
            second_path = project_service.projects[second_project]
            assert second_path == first_path

            # Clean up second project
            await project_service.remove_project(second_project)
        except ValueError:
            # This is expected if there's already a project with this exact path
            pass

        # Clean up
        await project_service.remove_project(first_project)


@pytest.mark.asyncio
async def test_add_project_rejects_nested_child_path(project_service: ProjectService):
    """Test that adding a project nested under an existing project fails."""
    parent_project_name = f"parent-project-{os.urandom(4).hex()}"
    # Use a completely separate temp directory to avoid fixture conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        parent_path = (test_root / "parent").as_posix()

        # Create parent directory
        os.makedirs(parent_path, exist_ok=True)

        try:
            # Add parent project
            await project_service.add_project(parent_project_name, parent_path)

            # Try to add a child project nested under parent
            child_project_name = f"child-project-{os.urandom(4).hex()}"
            child_path = (test_root / "parent" / "child").as_posix()

            with pytest.raises(ValueError, match="nested within existing project"):
                await project_service.add_project(child_project_name, child_path)

        finally:
            # Clean up
            if parent_project_name in project_service.projects:
                await project_service.remove_project(parent_project_name)


@pytest.mark.asyncio
async def test_add_project_rejects_parent_path_over_existing_child(project_service: ProjectService):
    """Test that adding a parent project over an existing nested project fails."""
    child_project_name = f"child-project-{os.urandom(4).hex()}"
    # Use a completely separate temp directory to avoid fixture conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        child_path = (test_root / "parent" / "child").as_posix()

        # Create child directory
        os.makedirs(child_path, exist_ok=True)

        try:
            # Add child project
            await project_service.add_project(child_project_name, child_path)

            # Try to add a parent project that contains the child
            parent_project_name = f"parent-project-{os.urandom(4).hex()}"
            parent_path = (test_root / "parent").as_posix()

            with pytest.raises(ValueError, match="is nested within this path"):
                await project_service.add_project(parent_project_name, parent_path)

        finally:
            # Clean up
            if child_project_name in project_service.projects:
                await project_service.remove_project(child_project_name)


@pytest.mark.asyncio
async def test_add_project_allows_sibling_paths(project_service: ProjectService):
    """Test that adding sibling projects (same level, different directories) succeeds."""
    project1_name = f"sibling-project-1-{os.urandom(4).hex()}"
    project2_name = f"sibling-project-2-{os.urandom(4).hex()}"

    # Use a completely separate temp directory to avoid fixture conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        project1_path = (test_root / "sibling1").as_posix()
        project2_path = (test_root / "sibling2").as_posix()

        # Create directories
        os.makedirs(project1_path, exist_ok=True)
        os.makedirs(project2_path, exist_ok=True)

        try:
            # Add first sibling project
            await project_service.add_project(project1_name, project1_path)

            # Add second sibling project (should succeed)
            await project_service.add_project(project2_name, project2_path)

            # Verify both exist
            assert project1_name in project_service.projects
            assert project2_name in project_service.projects

        finally:
            # Clean up
            if project1_name in project_service.projects:
                await project_service.remove_project(project1_name)
            if project2_name in project_service.projects:
                await project_service.remove_project(project2_name)


@pytest.mark.asyncio
async def test_add_project_rejects_deeply_nested_path(project_service: ProjectService):
    """Test that deeply nested paths are also rejected."""
    root_project_name = f"root-project-{os.urandom(4).hex()}"

    # Use a completely separate temp directory to avoid fixture conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        root_path = (test_root / "root").as_posix()

        # Create root directory
        os.makedirs(root_path, exist_ok=True)

        try:
            # Add root project
            await project_service.add_project(root_project_name, root_path)

            # Try to add a deeply nested project
            nested_project_name = f"nested-project-{os.urandom(4).hex()}"
            nested_path = (test_root / "root" / "level1" / "level2" / "level3").as_posix()

            with pytest.raises(ValueError, match="nested within existing project"):
                await project_service.add_project(nested_project_name, nested_path)

        finally:
            # Clean up
            if root_project_name in project_service.projects:
                await project_service.remove_project(root_project_name)


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_nested_validation_with_project_root(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that nested path validation works with BASIC_MEMORY_PROJECT_ROOT set."""
    # Use a completely separate temp directory to avoid fixture conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        parent_project_name = f"cloud-parent-{os.urandom(4).hex()}"
        child_project_name = f"cloud-child-{os.urandom(4).hex()}"

        try:
            # Add parent project - user path is ignored, uses sanitized project name
            await project_service.add_project(parent_project_name, "parent-folder")

            # Verify it was created using sanitized project name, not user path
            assert parent_project_name in project_service.projects
            parent_actual_path = project_service.projects[parent_project_name]
            # Path should use sanitized project name (cloud-parent-xxx -> cloud-parent-xxx)
            # NOT the user-provided path "parent-folder"
            assert parent_project_name.lower() in parent_actual_path.lower()
            # Resolve both to handle macOS /private/var vs /var
            assert (
                Path(parent_actual_path).resolve().is_relative_to(Path(project_root_path).resolve())
            )

            # Nested projects should still be prevented, even with user path ignored
            # Since paths use project names, this won't actually be nested
            # But we can test that two projects can coexist
            await project_service.add_project(child_project_name, "parent-folder/child-folder")

            # Both should exist with their own paths
            assert child_project_name in project_service.projects
            child_actual_path = project_service.projects[child_project_name]
            assert child_project_name.lower() in child_actual_path.lower()

            # Clean up child
            await project_service.remove_project(child_project_name)

        finally:
            # Clean up
            if parent_project_name in project_service.projects:
                await project_service.remove_project(parent_project_name)


@pytest.mark.asyncio
async def test_synchronize_projects_removes_db_only_projects(project_service: ProjectService):
    """Test that synchronize_projects removes projects that exist in DB but not in config.

    This is a regression test for issue #193 where deleted projects would be re-added
    to config during synchronization, causing them to reappear after deletion.
    Config is the source of truth - if a project is deleted from config, it should be
    removed from the database during synchronization.
    """
    test_project_name = f"test-db-only-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = str(test_root / "test-db-only")

        # Make sure the test directory exists
        os.makedirs(test_project_path, exist_ok=True)

        try:
            # Add project to database only (not to config) - simulating orphaned DB entry
            project_data = {
                "name": test_project_name,
                "path": test_project_path,
                "permalink": test_project_name.lower().replace(" ", "-"),
                "is_active": True,
            }
            await project_service.repository.create(project_data)

            # Verify it exists in DB but not in config
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is not None
            assert test_project_name not in project_service.projects

            # Call synchronize_projects - this should remove the orphaned DB entry
            # because config is the source of truth
            await project_service.synchronize_projects()

            # Verify project was removed from database
            db_project_after = await project_service.repository.get_by_name(test_project_name)
            assert db_project_after is None, (
                "Project should be removed from DB when not in config (config is source of truth)"
            )

            # Verify it's still not in config
            assert test_project_name not in project_service.projects

        finally:
            # Clean up if needed
            db_project = await project_service.repository.get_by_name(test_project_name)
            if db_project:
                await project_service.repository.delete(db_project.id)


@pytest.mark.asyncio
async def test_remove_project_with_delete_notes_false(project_service: ProjectService):
    """Test that remove_project with delete_notes=False keeps directory intact."""
    test_project_name = f"test-remove-keep-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = test_root / "test-project"
        test_project_path.mkdir()
        test_file = test_project_path / "test.md"
        test_file.write_text("# Test Note")

        try:
            # Add project
            await project_service.add_project(test_project_name, str(test_project_path))

            # Verify project exists
            assert test_project_name in project_service.projects
            assert test_project_path.exists()
            assert test_file.exists()

            # Remove project without deleting notes (default behavior)
            await project_service.remove_project(test_project_name, delete_notes=False)

            # Verify project is removed from config/db
            assert test_project_name not in project_service.projects
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is None

            # Verify directory and files still exist
            assert test_project_path.exists()
            assert test_file.exists()

        finally:
            # Cleanup happens automatically with temp_dir context manager
            pass


@pytest.mark.asyncio
async def test_remove_project_with_delete_notes_true(project_service: ProjectService):
    """Test that remove_project with delete_notes=True deletes directory."""
    test_project_name = f"test-remove-delete-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_project_path = test_root / "test-project"
        test_project_path.mkdir()
        test_file = test_project_path / "test.md"
        test_file.write_text("# Test Note")

        try:
            # Add project
            await project_service.add_project(test_project_name, str(test_project_path))

            # Verify project exists
            assert test_project_name in project_service.projects
            assert test_project_path.exists()
            assert test_file.exists()

            # Remove project with delete_notes=True
            await project_service.remove_project(test_project_name, delete_notes=True)

            # Verify project is removed from config/db
            assert test_project_name not in project_service.projects
            db_project = await project_service.repository.get_by_name(test_project_name)
            assert db_project is None

            # Verify directory and files are deleted
            assert not test_project_path.exists()

        finally:
            # Cleanup happens automatically with temp_dir context manager
            pass


@pytest.mark.asyncio
async def test_remove_project_delete_notes_missing_directory(project_service: ProjectService):
    """Test that remove_project with delete_notes=True handles missing directory gracefully."""
    test_project_name = f"test-remove-missing-{os.urandom(4).hex()}"
    test_project_path = f"/tmp/nonexistent-directory-{os.urandom(8).hex()}"

    try:
        # Add project pointing to non-existent path
        await project_service.add_project(test_project_name, test_project_path)

        # Verify project exists in config/db
        assert test_project_name in project_service.projects
        db_project = await project_service.repository.get_by_name(test_project_name)
        assert db_project is not None

        # Remove project with delete_notes=True (should not fail even if dir doesn't exist)
        await project_service.remove_project(test_project_name, delete_notes=True)

        # Verify project is removed from config/db
        assert test_project_name not in project_service.projects
        db_project = await project_service.repository.get_by_name(test_project_name)
        assert db_project is None

    finally:
        # Ensure cleanup
        if test_project_name in project_service.projects:
            try:
                project_service.config_manager.remove_project(test_project_name)
            except Exception:
                pass
