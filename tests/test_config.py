# """Tests for the Basic Memory configuration system."""
#
# from pathlib import Path
# from tempfile import TemporaryDirectory
#
# import pytest
#
# from basic_memory.config import (
#     BasicMemoryConfig,
#     ConfigManager,
#     DATA_DIR_NAME,
#     CONFIG_FILE_NAME,
#     APP_DATABASE_NAME,
#     get_project_config,
#     config_manager as module_config_manager,
# )
#
#
# class TestBasicMemoryConfig:
#     """Test the BasicMemoryConfig pydantic model."""
#
#     def test_default_values(self):
#         """Test that default values are set correctly."""
#         config = BasicMemoryConfig()
#         assert "main" in config.projects
#         assert config.default_project == "main"
#
#     def test_model_post_init(self, tmp_path):
#         """Test that model_post_init ensures valid configuration."""
#         # Test with empty projects
#         config = BasicMemoryConfig(projects={}, default_project="nonexistent")
#         assert "main" in config.projects
#         assert config.default_project == "main"
#
#         # Test with invalid default project
#         config = BasicMemoryConfig(
#             projects={"project1": f"{tmp_path}/path/to/project1"}, default_project="nonexistent"
#         )
#         assert "main" in config.projects
#         assert config.default_project == "main"
#
#     def test_custom_values(self, tmp_path):
#         """Test with custom values."""
#         config = BasicMemoryConfig(
#             projects={"project1": f"{tmp_path}/path/to/project1"}, default_project="project1"
#         )
#         assert config.projects["project1"] == f"{tmp_path}/path/to/project1"
#         assert config.default_project == "project1"
#         # Main should still be added automatically
#         assert "main" in config.projects
#
#     def test_app_database_path(self, monkeypatch):
#         """Test that app_database_path property returns the correct path."""
#         with TemporaryDirectory() as tempdir:
#             temp_home = Path(tempdir)
#             monkeypatch.setattr(Path, "home", lambda: temp_home)
#
#             config = BasicMemoryConfig()
#             expected_path = temp_home / DATA_DIR_NAME / APP_DATABASE_NAME
#
#             # The property should create the directory and touch the file
#             assert config.app_database_path == expected_path
#             assert expected_path.exists()
#
#             # The path should point to the app directory, not project directory
#             assert config.app_database_path.parent == temp_home / DATA_DIR_NAME
#
#     def test_database_path(self, monkeypatch):
#         """Test that database_path returns the app-level database path."""
#         with TemporaryDirectory() as tempdir:
#             temp_home = Path(tempdir)
#             monkeypatch.setattr(Path, "home", lambda: temp_home)
#
#             # Create a test configuration
#             app_config = BasicMemoryConfig(env="test")
#
#             # The database_path should point to the app-level database
#             app_db_path = temp_home / DATA_DIR_NAME / APP_DATABASE_NAME
#             assert app_config.database_path == app_db_path
#
#
# class TestConfigManager:
#     """Test the ConfigManager class."""
#
#     @pytest.fixture
#     def temp_home(self, monkeypatch):
#         """Create a temporary directory for testing."""
#         with TemporaryDirectory() as tempdir:
#             temp_home = Path(tempdir)
#             monkeypatch.setattr(Path, "home", lambda: temp_home)
#             yield temp_home
#
#     def test_init_creates_config_dir(self, temp_home):
#         """Test that init creates the config directory."""
#         config_manager = ConfigManager()
#         assert config_manager.config_dir.exists()
#         assert config_manager.config_dir == temp_home / ".basic-memory"
#
#     def test_init_creates_default_config(self, temp_home):
#         """Test that init creates a default config if none exists."""
#         config_manager = ConfigManager()
#         assert config_manager.config_file.exists()
#         assert "main" in config_manager.projects
#         assert config_manager.default_project == "main"
#
#     def test_current_project_id(self, temp_home):
#         """Test setting and getting current project ID."""
#         config_manager = ConfigManager()
#
#         # Set project ID
#         project_id = 42
#         config_manager.current_project_id = project_id
#
#         # Verify it was set
#         assert config_manager.current_project_id == project_id
#
#     def test_save_and_load_config(self, temp_home):
#         """Test saving and loading configuration."""
#         config_manager = ConfigManager()
#         # Add a project
#         config_manager.add_project("test", str(temp_home / "test-project"))
#         # Set as default
#         config_manager.set_default_project("test")
#
#         # Create a new manager to load from file
#         new_manager = ConfigManager()
#         assert "test" in new_manager.projects
#         assert new_manager.default_project == "test"
#         assert Path(new_manager.projects["test"]) == temp_home / "test-project"
#
#     def test_get_project_path(self, temp_home):
#         """Test getting a project path."""
#         config_manager = ConfigManager()
#         config_manager.add_project("test", str(temp_home / "test-project"))
#
#         # Get by name
#         path = config_manager.config.get_project_path(project_name="test")
#         assert path == temp_home / "test-project"
#
#         # Get default
#         path = config_manager.config.get_project_path()
#         assert path == temp_home / "basic-memory"
#
#         # Project does not exist
#         with pytest.raises(ValueError):
#             config_manager.config.get_project_path("nonexistent")
#
#     def test_environment_variable(self, temp_home, monkeypatch):
#         """Test using environment variable to select project."""
#         try:
#             # Set environment variable
#             monkeypatch.setenv("BASIC_MEMORY_PROJECT", "env_test")
#
#             # override the home path for the config manager
#             config_manager = module_config_manager
#             config_manager.config_dir = temp_home / ".basic-memory"
#             config_manager.config_dir.mkdir(parents=True, exist_ok=True)
#
#             config_manager.config_file = config_manager.config_dir / CONFIG_FILE_NAME
#
#             # add a project
#             config_manager.add_project("env_test", str(temp_home / "env_test"))
#
#             # Get project without specifying name
#             path = get_project_config().home
#             assert str(path) == str(temp_home / "env_test")
#         finally:
#             monkeypatch.delenv("BASIC_MEMORY_PROJECT")
#
#     def test_remove_project(self, temp_home):
#         """Test removing a project."""
#         config_manager = ConfigManager()
#         config_manager.add_project("test", str(temp_home / "test-project"))
#
#         # Remove project
#         config_manager.remove_project("test")
#         assert "test" not in config_manager.projects
#
#         # Cannot remove default project
#         with pytest.raises(ValueError):
#             config_manager.remove_project("main")
#
#         # Cannot remove nonexistent project
#         with pytest.raises(ValueError):
#             config_manager.remove_project("nonexistent")
#
#     def test_load_invalid_config(self, temp_home):
#         """Test loading invalid configuration."""
#         # Create invalid config file
#         config_dir = temp_home / DATA_DIR_NAME
#         config_dir.mkdir(parents=True, exist_ok=True)
#         config_file = config_dir / CONFIG_FILE_NAME
#         config_file.write_text("invalid json")
#
#         # Load config
#         config_manager = ConfigManager()
#
#         # Should have default config
#         assert "main" in config_manager.projects
#         assert config_manager.default_project == "main"
#
#     def test_save_config_error(self, temp_home, monkeypatch):
#         """Test error when saving configuration."""
#         # Create config manager
#         config_manager = ConfigManager()
#
#         # Make write_text raise an exception
#         def mock_write_text(content):
#             raise PermissionError("Permission denied")
#
#         monkeypatch.setattr(Path, "write_text", mock_write_text)
#
#         # Should not raise exception
#         config_manager.save_config(config_manager.config)
