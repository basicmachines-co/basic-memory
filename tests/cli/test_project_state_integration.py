"""Integration tests for project state consistency issues."""

import asyncio
import tempfile
from pathlib import Path
from typing import Generator
import pytest

from basic_memory.config import ConfigManager, BasicMemoryConfig, config_manager
from basic_memory.mcp.project_session import ProjectSession, session
from basic_memory.services.project_service import ProjectService
from basic_memory.repository.project_repository import ProjectRepository


@pytest.fixture
def isolated_config() -> Generator[tuple[ConfigManager, Path], None, None]:
    """Create an isolated config environment for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create test projects
        main_project = temp_path / "main"
        minerva_project = temp_path / "minerva"
        main_project.mkdir(parents=True, exist_ok=True)
        minerva_project.mkdir(parents=True, exist_ok=True)
        
        # Create isolated config manager
        test_manager = ConfigManager()
        test_manager.config_dir = temp_path
        test_manager.config_file = temp_path / "config.json"
        
        test_config = BasicMemoryConfig(
            projects={
                "main": str(main_project),
                "minerva": str(minerva_project)
            },
            default_project="main"
        )
        test_manager.config = test_config
        test_manager.save_config(test_config)
        
        yield test_manager, temp_path


class TestProjectStateIntegration:
    """Integration tests that reproduce the exact issues described in the bug report."""

    async def test_reproduce_mcp_cli_state_divergence(
        self, 
        isolated_config: tuple[ConfigManager, Path],
        session_maker,
        project_repository: ProjectRepository
    ):
        """Reproduce the exact issue: MCP and CLI showing different project states."""
        config_mgr, temp_path = isolated_config
        
        # Set up database with projects
        for name, path in config_mgr.projects.items():
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
        
        # Initialize MCP session (simulating server startup)
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Initialize project service
        project_service = ProjectService(project_repository)
        
        # Step 1: Set default project via CLI (simulated via service)
        await project_service.set_default_project("minerva")
        
        # Step 2: Check MCP state vs config state
        mcp_current = test_session.get_current_project()  # Should be "main" (old state)
        config_default = config_mgr.load_config().default_project  # Should be "minerva" (new state)
        
        # This reproduces the exact divergence described in the issue
        assert mcp_current == "main"
        assert config_default == "minerva"
        
        # This is the root cause of the "Project 'minerva' not found" error
        # because CLI commands use config but MCP session uses old state

    async def test_reproduce_edit_note_failure(
        self,
        isolated_config: tuple[ConfigManager, Path],
        session_maker,
        project_repository: ProjectRepository
    ):
        """Reproduce the edit note failure when projects are out of sync."""
        config_mgr, temp_path = isolated_config
        
        # Set up database with projects
        for name, path in config_mgr.projects.items():
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
        
        # Initialize session
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Change default project but don't update session
        project_service = ProjectService(project_repository)
        await project_service.set_default_project("minerva")
        
        # Now when edit_note tries to resolve project context:
        # - Session says current project is "main"
        # - Config says default project is "minerva"
        # - This mismatch causes identifier resolution issues
        
        assert test_session.get_current_project() == "main"
        assert config_mgr.load_config().default_project == "minerva"

    async def test_reproduce_status_command_failure(
        self,
        isolated_config: tuple[ConfigManager, Path],
        session_maker,
        project_repository: ProjectRepository
    ):
        """Reproduce the status command failure after default project change."""
        config_mgr, temp_path = isolated_config
        
        # Set up database with projects but make minerva project not exist in database initially
        main_data = {
            "name": "main",
            "path": str(config_mgr.projects["main"]),
            "permalink": "main",
            "is_active": True,
        }
        await project_repository.create(main_data)
        main_project = await project_repository.get_by_name("main")
        await project_repository.set_as_default(main_project.id)
        
        # Change config to set minerva as default but don't create minerva in database
        config_mgr.set_default_project("minerva")
        
        # Now when status command runs, it will:
        # 1. Read config.project which points to "minerva" 
        # 2. Try to find project in database
        # 3. Fail with "Project 'minerva' not found"
        
        config_default = config_mgr.load_config().default_project
        assert config_default == "minerva"
        
        # This would fail in the status command:
        minerva_project = await project_repository.get_by_name("minerva")
        assert minerva_project is None  # This is what causes the error

    def test_session_reinitialize_fixes_issue(self):
        """Test that reinitializing the session fixes the consistency issue."""
        # Initialize session with main
        test_session = ProjectSession()
        test_session.initialize("main")
        
        assert test_session.get_current_project() == "main"
        assert test_session.get_default_project() == "main"
        
        # Reinitialize with new default
        test_session.initialize("minerva")
        
        assert test_session.get_current_project() == "minerva"
        assert test_session.get_default_project() == "minerva"
        
        # This shows the fix is simple: reinitialize session when default changes