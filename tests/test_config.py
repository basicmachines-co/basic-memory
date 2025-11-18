"""Test configuration management."""

import tempfile
import pytest
from datetime import datetime

from basic_memory.config import BasicMemoryConfig, CloudProjectConfig, ConfigManager
from pathlib import Path


class TestBasicMemoryConfig:
    """Test BasicMemoryConfig behavior with BASIC_MEMORY_HOME environment variable."""

    def test_default_behavior_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that config uses default path when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig()

        # Should use the default path (home/basic-memory)
        expected_path = config_home / "basic-memory"
        assert Path(config.projects["main"]) == expected_path

    def test_respects_basic_memory_home_environment_variable(self, config_home, monkeypatch):
        """Test that config respects BASIC_MEMORY_HOME environment variable."""
        custom_path = config_home / "app" / "data"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        config = BasicMemoryConfig()

        # Should use the custom path from environment variable
        assert Path(config.projects["main"]) == custom_path

    def test_model_post_init_respects_basic_memory_home_creates_main(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init creates main project with BASIC_MEMORY_HOME when missing and no other projects."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        # Create config without main project
        config = BasicMemoryConfig()

        # model_post_init should have added main project with BASIC_MEMORY_HOME
        assert "main" in config.projects
        assert Path(config.projects["main"]) == custom_path

    def test_model_post_init_respects_basic_memory_home_sets_non_main_default(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init does not create main project with BASIC_MEMORY_HOME when another project exists."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = BasicMemoryConfig(projects={"other": str(other_path)})

        # model_post_init should not add main project with BASIC_MEMORY_HOME
        assert "main" not in config.projects
        assert Path(config.projects["other"]) == other_path

    def test_model_post_init_fallback_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that model_post_init can set a non-main default when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = BasicMemoryConfig(projects={"other": str(other_path)})

        # model_post_init should not add main project, but "other" should now be the default
        assert "main" not in config.projects
        assert Path(config.projects["other"]) == other_path

    def test_basic_memory_home_with_relative_path(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME works with relative paths."""
        relative_path = "relative/memory/path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", relative_path)

        config = BasicMemoryConfig()

        # Should normalize to platform-native path format
        assert Path(config.projects["main"]) == Path(relative_path)

    def test_basic_memory_home_overrides_existing_main_project(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME is not used when a map is passed in the constructor."""
        custom_path = str(config_home / "override" / "memory" / "path")
        monkeypatch.setenv("BASIC_MEMORY_HOME", custom_path)

        # Try to create config with a different main project path
        original_path = str(config_home / "original" / "path")
        config = BasicMemoryConfig(projects={"main": original_path})

        # The default_factory should override with BASIC_MEMORY_HOME value
        # Note: This tests the current behavior where default_factory takes precedence
        assert config.projects["main"] == original_path


class TestConfigManager:
    """Test ConfigManager functionality."""

    @pytest.fixture
    def temp_config_manager(self):
        """Create a ConfigManager with temporary config file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create a test ConfigManager instance
            config_manager = ConfigManager()
            # Override config paths to use temp directory
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.yaml"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create initial config with test projects
            test_config = BasicMemoryConfig(
                default_project="main",
                projects={
                    "main": str(temp_path / "main"),
                    "test-project": str(temp_path / "test"),
                    "special-chars": str(
                        temp_path / "special"
                    ),  # This will be the config key for "Special/Chars"
                },
            )
            config_manager.save_config(test_config)

            yield config_manager

    def test_set_default_project_with_exact_name_match(self, temp_config_manager):
        """Test set_default_project when project name matches config key exactly."""
        config_manager = temp_config_manager

        # Set default to a project that exists with exact name match
        config_manager.set_default_project("test-project")

        # Verify the config was updated
        config = config_manager.load_config()
        assert config.default_project == "test-project"

    def test_set_default_project_with_permalink_lookup(self, temp_config_manager):
        """Test set_default_project when input needs permalink normalization."""
        config_manager = temp_config_manager

        # Simulate a project that was created with special characters
        # The config key would be the permalink, but user might type the original name

        # First add a project with original name that gets normalized
        config = config_manager.load_config()
        config.projects["special-chars-project"] = str(Path("/tmp/special"))
        config_manager.save_config(config)

        # Now test setting default using a name that will normalize to the config key
        config_manager.set_default_project(
            "Special Chars Project"
        )  # This should normalize to "special-chars-project"

        # Verify the config was updated with the correct config key
        updated_config = config_manager.load_config()
        assert updated_config.default_project == "special-chars-project"

    def test_set_default_project_uses_canonical_name(self, temp_config_manager):
        """Test that set_default_project uses the canonical config key, not user input."""
        config_manager = temp_config_manager

        # Add a project with a config key that differs from user input
        config = config_manager.load_config()
        config.projects["my-test-project"] = str(Path("/tmp/mytest"))
        config_manager.save_config(config)

        # Set default using input that will match but is different from config key
        config_manager.set_default_project("My Test Project")  # Should find "my-test-project"

        # Verify that the canonical config key is used, not the user input
        updated_config = config_manager.load_config()
        assert updated_config.default_project == "my-test-project"
        # Should NOT be the user input
        assert updated_config.default_project != "My Test Project"

    def test_set_default_project_nonexistent_project(self, temp_config_manager):
        """Test set_default_project raises ValueError for nonexistent project."""
        config_manager = temp_config_manager

        with pytest.raises(ValueError, match="Project 'nonexistent' not found"):
            config_manager.set_default_project("nonexistent")

    def test_disable_permalinks_flag_default(self):
        """Test that disable_permalinks flag defaults to False."""
        config = BasicMemoryConfig()
        assert config.disable_permalinks is False

    def test_disable_permalinks_flag_can_be_enabled(self):
        """Test that disable_permalinks flag can be set to True."""
        config = BasicMemoryConfig(disable_permalinks=True)
        assert config.disable_permalinks is True

    def test_config_manager_respects_custom_config_dir(self, monkeypatch):
        """Test that ConfigManager respects BASIC_MEMORY_CONFIG_DIR environment variable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_config_dir = Path(temp_dir) / "custom" / "config"
            monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_config_dir))

            config_manager = ConfigManager()

            # Verify config_dir is set to the custom path
            assert config_manager.config_dir == custom_config_dir
            # Verify config_file is in the custom directory
            assert config_manager.config_file == custom_config_dir / "config.json"
            # Verify the directory was created
            assert config_manager.config_dir.exists()

    def test_config_manager_default_without_custom_config_dir(self, config_home, monkeypatch):
        """Test that ConfigManager uses default location when BASIC_MEMORY_CONFIG_DIR is not set."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)

        config_manager = ConfigManager()

        # Should use default location
        assert config_manager.config_dir == config_home / ".basic-memory"
        assert config_manager.config_file == config_home / ".basic-memory" / "config.json"

    def test_remove_project_with_exact_name_match(self, temp_config_manager):
        """Test remove_project when project name matches config key exactly."""
        config_manager = temp_config_manager

        # Verify project exists
        config = config_manager.load_config()
        assert "test-project" in config.projects

        # Remove the project with exact name match
        config_manager.remove_project("test-project")

        # Verify the project was removed
        config = config_manager.load_config()
        assert "test-project" not in config.projects

    def test_remove_project_with_permalink_lookup(self, temp_config_manager):
        """Test remove_project when input needs permalink normalization."""
        config_manager = temp_config_manager

        # Add a project with normalized key
        config = config_manager.load_config()
        config.projects["special-chars-project"] = str(Path("/tmp/special"))
        config_manager.save_config(config)

        # Remove using a name that will normalize to the config key
        config_manager.remove_project(
            "Special Chars Project"
        )  # This should normalize to "special-chars-project"

        # Verify the project was removed using the correct config key
        updated_config = config_manager.load_config()
        assert "special-chars-project" not in updated_config.projects

    def test_remove_project_uses_canonical_name(self, temp_config_manager):
        """Test that remove_project uses the canonical config key, not user input."""
        config_manager = temp_config_manager

        # Add a project with a config key that differs from user input
        config = config_manager.load_config()
        config.projects["my-test-project"] = str(Path("/tmp/mytest"))
        config_manager.save_config(config)

        # Remove using input that will match but is different from config key
        config_manager.remove_project("My Test Project")  # Should find "my-test-project"

        # Verify that the canonical config key was removed
        updated_config = config_manager.load_config()
        assert "my-test-project" not in updated_config.projects

    def test_remove_project_nonexistent_project(self, temp_config_manager):
        """Test remove_project raises ValueError for nonexistent project."""
        config_manager = temp_config_manager

        with pytest.raises(ValueError, match="Project 'nonexistent' not found"):
            config_manager.remove_project("nonexistent")

    def test_remove_project_cannot_remove_default(self, temp_config_manager):
        """Test remove_project raises ValueError when trying to remove default project."""
        config_manager = temp_config_manager

        # Try to remove the default project
        with pytest.raises(ValueError, match="Cannot remove the default project"):
            config_manager.remove_project("main")

    def test_config_with_cloud_projects_empty_by_default(self, temp_config_manager):
        """Test that cloud_projects field exists and defaults to empty dict."""
        config_manager = temp_config_manager
        config = config_manager.load_config()

        assert hasattr(config, "cloud_projects")
        assert config.cloud_projects == {}

    def test_save_and_load_config_with_cloud_projects(self):
        """Test that config with cloud_projects can be saved and loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create config with cloud_projects
            now = datetime.now()
            test_config = BasicMemoryConfig(
                projects={"main": str(temp_path / "main")},
                cloud_projects={
                    "research": CloudProjectConfig(
                        local_path=str(temp_path / "research-local"),
                        last_sync=now,
                        bisync_initialized=True,
                    )
                },
            )
            config_manager.save_config(test_config)

            # Load and verify
            loaded_config = config_manager.load_config()
            assert "research" in loaded_config.cloud_projects
            assert loaded_config.cloud_projects["research"].local_path == str(
                temp_path / "research-local"
            )
            assert loaded_config.cloud_projects["research"].bisync_initialized is True
            assert loaded_config.cloud_projects["research"].last_sync == now

    def test_add_cloud_project_to_existing_config(self):
        """Test adding cloud projects to an existing config file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create initial config without cloud projects
            initial_config = BasicMemoryConfig(projects={"main": str(temp_path / "main")})
            config_manager.save_config(initial_config)

            # Load, modify, and save
            config = config_manager.load_config()
            assert config.cloud_projects == {}

            config.cloud_projects["work"] = CloudProjectConfig(
                local_path=str(temp_path / "work-local")
            )
            config_manager.save_config(config)

            # Reload and verify persistence
            reloaded_config = config_manager.load_config()
            assert "work" in reloaded_config.cloud_projects
            assert reloaded_config.cloud_projects["work"].local_path == str(
                temp_path / "work-local"
            )
            assert reloaded_config.cloud_projects["work"].bisync_initialized is False

    def test_backward_compatibility_loading_config_without_cloud_projects(self):
        """Test that old config files without cloud_projects field can be loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Manually write old-style config without cloud_projects
            import json

            old_config_data = {
                "env": "dev",
                "projects": {"main": str(temp_path / "main")},
                "default_project": "main",
                "log_level": "INFO",
            }
            config_manager.config_file.write_text(json.dumps(old_config_data, indent=2))

            # Clear the config cache to ensure we load from the temp file
            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None

            # Should load successfully with cloud_projects defaulting to empty dict
            config = config_manager.load_config()
            assert config.cloud_projects == {}
            assert config.projects == {"main": str(temp_path / "main")}


class TestPlatformNativePathSeparators:
    """Test that config uses platform-native path separators."""

    def test_project_paths_use_platform_native_separators_in_config(self, monkeypatch):
        """Test that project paths use platform-native separators when created."""
        import platform

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Set up ConfigManager with temp directory
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create a project path
            project_path = temp_path / "my" / "project"
            project_path.mkdir(parents=True, exist_ok=True)

            # Add project via ConfigManager
            config = BasicMemoryConfig(projects={})
            config.projects["test-project"] = str(project_path)
            config_manager.save_config(config)

            # Read the raw JSON file
            import json

            config_data = json.loads(config_manager.config_file.read_text())

            # Verify path uses platform-native separators
            saved_path = config_data["projects"]["test-project"]

            # On Windows, should have backslashes; on Unix, forward slashes
            if platform.system() == "Windows":
                # Windows paths should contain backslashes
                assert "\\" in saved_path or ":" in saved_path  # C:\\ or \\UNC
                assert "/" not in saved_path.replace(":/", "")  # Exclude drive letter
            else:
                # Unix paths should use forward slashes
                assert "/" in saved_path
                # Should not force POSIX on non-Windows
                assert saved_path == str(project_path)

    def test_add_project_uses_platform_native_separators(self, monkeypatch):
        """Test that ConfigManager.add_project() uses platform-native separators."""
        import platform

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Set up ConfigManager
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Initialize with empty projects
            initial_config = BasicMemoryConfig(projects={})
            config_manager.save_config(initial_config)

            # Add project
            project_path = temp_path / "new" / "project"
            config_manager.add_project("new-project", str(project_path))

            # Load and verify
            config = config_manager.load_config()
            saved_path = config.projects["new-project"]

            # Verify platform-native separators
            if platform.system() == "Windows":
                assert "\\" in saved_path or ":" in saved_path
            else:
                assert "/" in saved_path
                assert saved_path == str(project_path)

    def test_model_post_init_uses_platform_native_separators(self, config_home, monkeypatch):
        """Test that model_post_init uses platform-native separators."""
        import platform

        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Create config without projects (triggers model_post_init to add main)
        config = BasicMemoryConfig(projects={})

        # Verify main project path uses platform-native separators
        main_path = config.projects["main"]

        if platform.system() == "Windows":
            # Windows: should have backslashes or drive letter
            assert "\\" in main_path or ":" in main_path
        else:
            # Unix: should have forward slashes
            assert "/" in main_path
