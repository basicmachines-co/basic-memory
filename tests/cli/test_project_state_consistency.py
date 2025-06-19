"""Test project state consistency between MCP and CLI components."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from basic_memory import config
from basic_memory.config import ConfigManager, BasicMemoryConfig
from basic_memory.mcp.project_session import ProjectSession, session, get_active_project
from basic_memory.cli.commands.project import list_projects, set_default_project
from basic_memory.services.project_service import ProjectService
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.models.project import Project


@pytest.fixture
def temp_config_dir() -> Generator[Path, None, None]:
    """Create a temporary config directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        yield temp_path


@pytest.fixture
def temp_projects(temp_config_dir: Path) -> dict[str, Path]:
    """Create temporary project directories."""
    projects = {}
    for name in ["main", "minerva", "test-project"]:
        project_path = temp_config_dir / name
        project_path.mkdir(parents=True, exist_ok=True)
        projects[name] = project_path
    return projects


@pytest.fixture
def config_manager_with_projects(temp_config_dir: Path, temp_projects: dict[str, Path]) -> ConfigManager:
    """Create a config manager with test projects."""
    # Mock the config directory
    original_config_dir = config.config_manager.config_dir
    original_config_file = config.config_manager.config_file
    
    try:
        config.config_manager.config_dir = temp_config_dir
        config.config_manager.config_file = temp_config_dir / "config.json"
        
        # Create test config
        test_config = BasicMemoryConfig(
            projects={name: str(path) for name, path in temp_projects.items()},
            default_project="main"
        )
        config.config_manager.config = test_config
        config.config_manager.save_config(test_config)
        
        yield config.config_manager
    finally:
        # Restore original paths
        config.config_manager.config_dir = original_config_dir
        config.config_manager.config_file = original_config_file


class TestProjectStateConsistency:
    """Test cases for project state consistency between MCP and CLI."""

    def test_project_session_initialization(self, config_manager_with_projects: ConfigManager):
        """Test that project session initializes with correct default project."""
        # Create a new session
        test_session = ProjectSession()
        
        # Initialize with default project from config
        default_project = config_manager_with_projects.default_project
        test_session.initialize(default_project)
        
        assert test_session.get_current_project() == "main"
        assert test_session.get_default_project() == "main"

    def test_mcp_cli_project_state_mismatch(self, config_manager_with_projects: ConfigManager):
        """Test reproducing the MCP vs CLI project state mismatch."""
        # Initialize MCP session
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Simulate CLI setting default project to "minerva"
        config_manager_with_projects.set_default_project("minerva")
        
        # MCP session should still show "main" because it hasn't been reloaded
        assert test_session.get_current_project() == "main"
        
        # Config manager should show "minerva" as default
        assert config_manager_with_projects.default_project == "minerva"
        
        # This demonstrates the inconsistency issue

    def test_default_project_persistence(self, config_manager_with_projects: ConfigManager, temp_config_dir: Path):
        """Test that default project setting persists correctly."""
        # Set default project
        config_manager_with_projects.set_default_project("minerva")
        
        # Verify it's saved to config file
        config_file = temp_config_dir / "config.json"
        assert config_file.exists()
        
        # Create new config manager to simulate fresh load
        new_manager = ConfigManager()
        new_manager.config_dir = temp_config_dir
        new_manager.config_file = config_file
        reloaded_config = new_manager.load_config()
        
        assert reloaded_config.default_project == "minerva"

    async def test_project_not_found_after_default_change(self, config_manager_with_projects: ConfigManager):
        """Test reproducing the 'Project not found' error after default change."""
        # Initialize with main project
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Change default in config
        config_manager_with_projects.set_default_project("minerva")
        
        # Simulate the status command trying to access the project
        # This should fail because session still thinks current project is "main"
        # but config now says default is "minerva"
        
        current_from_session = test_session.get_current_project()  # Returns "main"
        current_from_config = config_manager_with_projects.default_project  # Returns "minerva"
        
        assert current_from_session != current_from_config
        # This mismatch is what causes the "Project not found" error

    def test_edit_note_identifier_resolution_issue(self, config_manager_with_projects: ConfigManager):
        """Test the edit note identifier resolution when project state is inconsistent."""
        # Initialize MCP session with one project
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Simulate project switch in config but not in session
        config_manager_with_projects.set_default_project("minerva")
        
        # When edit_note tries to use get_active_project, it might get confused
        # about which project context to use
        try:
            # This would normally call get_active_project(None) 
            # which should use session.get_current_project()
            active_project = get_active_project(None)
            # The project config returned might not match the actual session state
            assert active_project.name == "main"  # Session state
            
            # But config shows different default
            assert config_manager_with_projects.default_project == "minerva"
            
        except Exception as e:
            # This might fail if project lookup is inconsistent
            pytest.fail(f"get_active_project failed with inconsistent state: {e}")

    def test_session_reload_fixes_inconsistency(self, config_manager_with_projects: ConfigManager):
        """Test that reloading session fixes the inconsistency."""
        # Initialize session
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Change config
        config_manager_with_projects.set_default_project("minerva")
        
        # Verify inconsistency
        assert test_session.get_current_project() == "main"
        assert config_manager_with_projects.default_project == "minerva"
        
        # Reinitialize session with new default
        new_default = config_manager_with_projects.default_project
        test_session.initialize(new_default)
        
        # Now they should match
        assert test_session.get_current_project() == "minerva"
        assert config_manager_with_projects.default_project == "minerva"

    async def test_project_service_default_setting_propagation(
        self, 
        config_manager_with_projects: ConfigManager,
        session_maker,
        project_repository: ProjectRepository
    ):
        """Test that project service properly propagates default changes."""
        # Create projects in database to match config
        for name, path in config_manager_with_projects.projects.items():
            project_data = {
                "name": name,
                "path": str(path),
                "permalink": name.lower().replace(" ", "-"),
                "is_active": True,
            }
            await project_repository.create(project_data)
        
        # Set main as default in database
        main_project = await project_repository.get_by_name("main")
        await project_repository.set_as_default(main_project.id)
        
        # Initialize project service
        project_service = ProjectService(project_repository)
        
        # Change default via service
        await project_service.set_default_project("minerva")
        
        # Verify both config and database are updated
        assert config_manager_with_projects.default_project == "minerva"
        
        db_default = await project_repository.get_default_project()
        assert db_default is not None
        assert db_default.name == "minerva"

    def test_cli_config_reload_requirement(self, config_manager_with_projects: ConfigManager):
        """Test that CLI commands require config reload after default change."""
        # This test verifies the issue mentioned in the CLI command:
        # "Reload configuration to apply the change"
        
        # Change default project
        original_default = config_manager_with_projects.default_project
        config_manager_with_projects.set_default_project("minerva")
        
        # Without reload, global config might still show old value
        # This is what causes the inconsistency
        assert config_manager_with_projects.default_project == "minerva"
        
        # But global config object might not be updated
        # (This would require actual module reload which we can't easily test)
        
        # The fix should ensure config changes are immediately visible
        # to all components without requiring restart