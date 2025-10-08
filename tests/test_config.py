"""Test configuration management."""

import tempfile
import pytest

from basic_memory.config import BasicMemoryConfig, ConfigManager
from pathlib import Path


class TestBasicMemoryConfig:
    """Test BasicMemoryConfig behavior with BASIC_MEMORY_HOME environment variable."""

    def test_default_behavior_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that config uses default path when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig()

        # Should use the default path (home/basic-memory)
        expected_path = (config_home / "basic-memory").as_posix()
        assert config.projects["main"] == Path(expected_path).as_posix()

    def test_respects_basic_memory_home_environment_variable(self, config_home, monkeypatch):
        """Test that config respects BASIC_MEMORY_HOME environment variable."""
        custom_path = (config_home / "app" / "data").as_posix()
        monkeypatch.setenv("BASIC_MEMORY_HOME", custom_path)

        config = BasicMemoryConfig()

        # Should use the custom path from environment variable
        assert config.projects["main"] == custom_path

    def test_model_post_init_respects_basic_memory_home(self, config_home, monkeypatch):
        """Test that model_post_init creates main project with BASIC_MEMORY_HOME when missing."""
        custom_path = str(config_home / "custom" / "memory" / "path")
        monkeypatch.setenv("BASIC_MEMORY_HOME", custom_path)

        # Create config without main project
        other_path = str(config_home / "some" / "path")
        config = BasicMemoryConfig(projects={"other": other_path})

        # model_post_init should have added main project with BASIC_MEMORY_HOME
        assert "main" in config.projects
        assert config.projects["main"] == Path(custom_path).as_posix()

    def test_model_post_init_fallback_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that model_post_init falls back to default when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Create config without main project
        other_path = (config_home / "some" / "path").as_posix()
        config = BasicMemoryConfig(projects={"other": other_path})

        # model_post_init should have added main project with default path
        expected_path = (config_home / "basic-memory").as_posix()
        assert "main" in config.projects
        assert config.projects["main"] == Path(expected_path).as_posix()

    def test_basic_memory_home_with_relative_path(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME works with relative paths."""
        relative_path = "relative/memory/path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", relative_path)

        config = BasicMemoryConfig()

        # Should use the exact value from environment variable
        assert config.projects["main"] == relative_path

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
