"""Test that the project session refresh fix resolves consistency issues."""

import tempfile
from pathlib import Path
from typing import Generator
import pytest

from basic_memory.config import ConfigManager, BasicMemoryConfig
from basic_memory.mcp.project_session import ProjectSession
from basic_memory.services.project_service import ProjectService
from basic_memory.repository.project_repository import ProjectRepository


@pytest.fixture
def temp_config_setup() -> Generator[tuple[ConfigManager, Path], None, None]:
    """Set up temporary config with test projects."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create project directories
        main_dir = temp_path / "main"
        minerva_dir = temp_path / "minerva"
        main_dir.mkdir(parents=True, exist_ok=True)
        minerva_dir.mkdir(parents=True, exist_ok=True)
        
        # Create isolated config manager
        config_mgr = ConfigManager()
        config_mgr.config_dir = temp_path
        config_mgr.config_file = temp_path / "config.json"
        
        # Initialize with test projects
        test_config = BasicMemoryConfig(
            projects={
                "main": str(main_dir),
                "minerva": str(minerva_dir)
            },
            default_project="main"
        )
        config_mgr.config = test_config
        config_mgr.save_config(test_config)
        
        yield config_mgr, temp_path


class TestProjectSessionFix:
    """Test the project session refresh functionality fixes consistency issues."""

    def test_session_refresh_from_config(self, temp_config_setup: tuple[ConfigManager, Path]):
        """Test that refresh_from_config updates session state correctly."""
        config_mgr, temp_path = temp_config_setup
        
        # Create a test session
        test_session = ProjectSession()
        test_session.initialize("main")
        
        assert test_session.get_current_project() == "main"
        assert test_session.get_default_project() == "main"
        
        # Change config externally
        config_mgr.set_default_project("minerva")
        
        # Session should still have old state
        assert test_session.get_current_project() == "main"
        
        # Mock the config_manager import in refresh_from_config
        # We need to temporarily replace the config_manager import
        import basic_memory.mcp.project_session as session_module
        original_import = session_module.config_manager
        
        try:
            # Replace with our test config manager
            session_module.config_manager = config_mgr
            
            # Now refresh should pick up the new config
            test_session.refresh_from_config()
            
            # Session should now have new state
            assert test_session.get_current_project() == "minerva"
            assert test_session.get_default_project() == "minerva"
            
        finally:
            # Restore original
            session_module.config_manager = original_import

    async def test_project_service_auto_refresh(
        self, 
        temp_config_setup: tuple[ConfigManager, Path],
        session_maker,
        project_repository: ProjectRepository
    ):
        """Test that project service automatically refreshes session on default change."""
        config_mgr, temp_path = temp_config_setup
        
        # Set up database projects
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
        
        # Create test session
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Mock the session import in project service
        import basic_memory.services.project_service as service_module
        import basic_memory.mcp.project_session as session_module
        
        original_session = session_module.session
        original_config_mgr = session_module.config_manager
        
        try:
            # Replace with our test instances
            session_module.session = test_session
            session_module.config_manager = config_mgr
            
            # Create project service
            project_service = ProjectService(project_repository)
            
            # Change default project via service
            await project_service.set_default_project("minerva")
            
            # Session should be automatically refreshed
            assert test_session.get_current_project() == "minerva"
            assert test_session.get_default_project() == "minerva"
            
            # Config should also be updated
            assert config_mgr.default_project == "minerva"
            
        finally:
            # Restore originals
            session_module.session = original_session
            session_module.config_manager = original_config_mgr

    async def test_fix_resolves_mcp_cli_divergence(
        self,
        temp_config_setup: tuple[ConfigManager, Path],
        session_maker,
        project_repository: ProjectRepository
    ):
        """Test that the fix resolves the MCP vs CLI state divergence issue."""
        config_mgr, temp_path = temp_config_setup
        
        # Set up database projects
        for name, path in config_mgr.projects.items():
            project_data = {
                "name": name,
                "path": str(path),
                "permalink": name.lower().replace(" ", "-"),
                "is_active": True,
            }
            await project_repository.create(project_data)
        
        main_project = await project_repository.get_by_name("main")
        await project_repository.set_as_default(main_project.id)
        
        # Create test session (simulating MCP server startup)
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Mock imports
        import basic_memory.mcp.project_session as session_module
        original_session = session_module.session
        original_config_mgr = session_module.config_manager
        
        try:
            session_module.session = test_session
            session_module.config_manager = config_mgr
            
            # Initial state - everything consistent
            assert test_session.get_current_project() == "main"
            assert config_mgr.default_project == "main"
            
            # Simulate CLI setting default project (via service)
            project_service = ProjectService(project_repository)
            await project_service.set_default_project("minerva")
            
            # After the fix, both should be consistent
            mcp_current = test_session.get_current_project()
            config_default = config_mgr.default_project
            
            assert mcp_current == "minerva"
            assert config_default == "minerva"
            assert mcp_current == config_default  # No more divergence!
            
        finally:
            session_module.session = original_session
            session_module.config_manager = original_config_mgr

    def test_refresh_handles_missing_project_gracefully(self, temp_config_setup: tuple[ConfigManager, Path]):
        """Test that refresh handles missing projects gracefully."""
        config_mgr, temp_path = temp_config_setup
        
        test_session = ProjectSession()
        test_session.initialize("main")
        
        # Change config to point to non-existent project
        config_mgr.config.default_project = "nonexistent"
        config_mgr.save_config(config_mgr.config)
        
        # Mock the import
        import basic_memory.mcp.project_session as session_module
        original_config_mgr = session_module.config_manager
        
        try:
            session_module.config_manager = config_mgr
            
            # Refresh should not crash even with invalid project
            test_session.refresh_from_config()
            
            # Should fallback to the configured default even if invalid
            assert test_session.get_default_project() == "nonexistent"
            
        finally:
            session_module.config_manager = original_config_mgr