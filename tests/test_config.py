"""Test configuration management."""

import tempfile
import pytest
from datetime import datetime

from basic_memory.config import (
    BasicMemoryConfig,
    ConfigManager,
    ProjectEntry,
    ProjectMode,
)
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
        assert Path(config.projects["main"].path) == expected_path

    def test_respects_basic_memory_home_environment_variable(self, config_home, monkeypatch):
        """Test that config respects BASIC_MEMORY_HOME environment variable."""
        custom_path = config_home / "app" / "data"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        config = BasicMemoryConfig()

        # Should use the custom path from environment variable
        assert Path(config.projects["main"].path) == custom_path

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
        assert Path(config.projects["main"].path) == custom_path

    def test_model_post_init_respects_basic_memory_home_sets_non_main_default(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init does not create main project with BASIC_MEMORY_HOME when another project exists."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = BasicMemoryConfig(projects={"other": {"path": str(other_path)}})

        # model_post_init should not add main project with BASIC_MEMORY_HOME
        assert "main" not in config.projects
        assert Path(config.projects["other"].path) == other_path

    def test_model_post_init_fallback_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that model_post_init can set a non-main default when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = BasicMemoryConfig(projects={"other": {"path": str(other_path)}})

        # model_post_init should not add main project, but "other" should now be the default
        assert "main" not in config.projects
        assert Path(config.projects["other"].path) == other_path

    def test_basic_memory_home_with_relative_path(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME works with relative paths."""
        relative_path = "relative/memory/path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", relative_path)

        config = BasicMemoryConfig()

        # Should normalize to platform-native path format
        assert Path(config.projects["main"].path) == Path(relative_path)

    def test_basic_memory_home_overrides_existing_main_project(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME is not used when a map is passed in the constructor."""
        custom_path = str(config_home / "override" / "memory" / "path")
        monkeypatch.setenv("BASIC_MEMORY_HOME", custom_path)

        # Try to create config with a different main project path
        original_path = str(config_home / "original" / "path")
        config = BasicMemoryConfig(projects={"main": {"path": original_path}})

        # The default_factory should override with BASIC_MEMORY_HOME value
        # Note: This tests the current behavior where default_factory takes precedence
        assert config.projects["main"].path == original_path

    def test_app_database_path_uses_custom_config_dir(self, tmp_path, monkeypatch):
        """Default SQLite DB should live under BASIC_MEMORY_CONFIG_DIR when set."""
        custom_config_dir = tmp_path / "instance-a" / "state"
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_config_dir))

        config = BasicMemoryConfig(projects={"main": {"path": str(tmp_path / "project")}})

        assert config.data_dir_path == custom_config_dir
        assert config.app_database_path == custom_config_dir / "memory.db"
        assert config.app_database_path.exists()

    def test_app_database_path_defaults_to_home_data_dir(self, config_home, monkeypatch):
        """Without BASIC_MEMORY_CONFIG_DIR, default DB stays at ~/.basic-memory/memory.db."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        config = BasicMemoryConfig()

        assert config.data_dir_path == config_home / ".basic-memory"
        assert config.app_database_path == config_home / ".basic-memory" / "memory.db"


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
                    "main": {"path": str(temp_path / "main")},
                    "test-project": {"path": str(temp_path / "test")},
                    "special-chars": {
                        "path": str(temp_path / "special")
                    },  # This will be the config key for "Special/Chars"
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
        config.projects["special-chars-project"] = ProjectEntry(path=str(Path("/tmp/special")))
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
        config.projects["my-test-project"] = ProjectEntry(path=str(Path("/tmp/mytest")))
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

    def test_ensure_frontmatter_on_sync_flag_default(self):
        """Test that ensure_frontmatter_on_sync defaults to False."""
        config = BasicMemoryConfig()
        assert config.ensure_frontmatter_on_sync is False

    def test_ensure_frontmatter_on_sync_flag_can_be_enabled(self):
        """Test that ensure_frontmatter_on_sync can be set to True."""
        config = BasicMemoryConfig(ensure_frontmatter_on_sync=True)
        assert config.ensure_frontmatter_on_sync is True

    def test_permalinks_include_project_flag_default(self):
        """Test that permalinks_include_project defaults to True."""
        config = BasicMemoryConfig()
        assert config.permalinks_include_project is True

    def test_permalinks_include_project_flag_can_be_disabled(self):
        """Test that permalinks_include_project can be set to False."""
        config = BasicMemoryConfig(permalinks_include_project=False)
        assert config.permalinks_include_project is False

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
        config.projects["special-chars-project"] = ProjectEntry(path=str(Path("/tmp/special")))
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
        config.projects["my-test-project"] = ProjectEntry(path=str(Path("/tmp/mytest")))
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

    def test_config_project_entry_cloud_sync_defaults(self, temp_config_manager):
        """Test that ProjectEntry cloud sync fields default to None/False."""
        config_manager = temp_config_manager
        config = config_manager.load_config()

        entry = config.projects["main"]
        assert entry.cloud_sync_path is None
        assert entry.bisync_initialized is False
        assert entry.last_sync is None

    def test_save_and_load_config_with_cloud_sync_fields(self):
        """Test that config with cloud sync fields can be saved and loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create config with cloud sync fields on a project entry
            now = datetime.now()
            test_config = BasicMemoryConfig(
                projects={
                    "main": {"path": str(temp_path / "main")},
                    "research": {
                        "path": str(temp_path / "research"),
                        "mode": "cloud",
                        "cloud_sync_path": str(temp_path / "research-local"),
                        "last_sync": now.isoformat(),
                        "bisync_initialized": True,
                    },
                },
            )
            config_manager.save_config(test_config)

            # Load and verify
            loaded_config = config_manager.load_config()
            assert "research" in loaded_config.projects
            entry = loaded_config.projects["research"]
            assert entry.cloud_sync_path == str(temp_path / "research-local")
            assert entry.bisync_initialized is True
            assert entry.last_sync == now

    def test_add_cloud_sync_to_existing_project(self):
        """Test adding cloud sync fields to an existing project entry."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create initial config without cloud sync fields
            initial_config = BasicMemoryConfig(projects={"main": {"path": str(temp_path / "main")}})
            config_manager.save_config(initial_config)

            # Load, modify, and save
            config = config_manager.load_config()
            assert config.projects["main"].cloud_sync_path is None

            config.projects["main"].cloud_sync_path = str(temp_path / "work-local")
            config_manager.save_config(config)

            # Reload and verify persistence
            reloaded_config = config_manager.load_config()
            assert reloaded_config.projects["main"].cloud_sync_path == str(temp_path / "work-local")
            assert reloaded_config.projects["main"].bisync_initialized is False

    def test_backward_compatibility_loading_old_format_config(self):
        """Test that old config files with Dict[str, str] projects can be loaded and migrated."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Manually write old-style config with Dict[str, str] projects
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

            # Should load successfully with migration to ProjectEntry
            config = config_manager.load_config()
            assert isinstance(config.projects["main"], ProjectEntry)
            assert config.projects["main"].path == str(temp_path / "main")
            assert config.projects["main"].mode == ProjectMode.LOCAL

    def test_backward_compatibility_migrates_project_modes_and_cloud_projects(self):
        """Test that old config with project_modes and cloud_projects is migrated."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            old_config_data = {
                "env": "dev",
                "projects": {
                    "main": str(temp_path / "main"),
                    "research": str(temp_path / "research"),
                },
                "default_project": "main",
                "project_modes": {"research": "cloud"},
                "cloud_projects": {
                    "research": {
                        "local_path": str(temp_path / "research-local"),
                        "bisync_initialized": True,
                        "last_sync": "2026-02-06T17:36:38",
                    }
                },
            }
            config_manager.config_file.write_text(json.dumps(old_config_data, indent=2))

            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None

            config = config_manager.load_config()

            # Verify migration
            assert config.projects["research"].mode == ProjectMode.CLOUD
            assert config.projects["research"].cloud_sync_path == str(temp_path / "research-local")
            assert config.projects["research"].bisync_initialized is True
            assert config.projects["main"].mode == ProjectMode.LOCAL

    def test_legacy_cloud_mode_key_is_stripped_on_normalization_save(self):
        """Legacy cloud_mode should be removed from config.json after load/save normalization."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            legacy_config = {
                "env": "dev",
                "projects": {"main": str(temp_path / "main")},
                "default_project": "main",
                "cloud_mode": True,
            }
            config_manager.config_file.write_text(json.dumps(legacy_config, indent=2))

            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None

            loaded = config_manager.load_config()
            assert isinstance(loaded, BasicMemoryConfig)

            raw = json.loads(config_manager.config_file.read_text(encoding="utf-8"))
            assert "cloud_mode" not in raw


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
            config.projects["test-project"] = ProjectEntry(path=str(project_path))
            config_manager.save_config(config)

            # Read the raw JSON file
            import json

            config_data = json.loads(config_manager.config_file.read_text())

            # Verify path uses platform-native separators
            saved_path = config_data["projects"]["test-project"]["path"]

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
            saved_path = config.projects["new-project"].path

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
        main_path = config.projects["main"].path

        if platform.system() == "Windows":
            # Windows: should have backslashes or drive letter
            assert "\\" in main_path or ":" in main_path
        else:
            # Unix: should have forward slashes
            assert "/" in main_path


class TestSemanticSearchConfig:
    """Test semantic search configuration options."""

    def test_semantic_search_enabled_defaults_to_true_when_semantic_modules_are_available(
        self, monkeypatch
    ):
        """Semantic search defaults on when fastembed and sqlite_vec are importable."""
        import basic_memory.config as config_module

        monkeypatch.delenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", raising=False)
        monkeypatch.setattr(
            config_module.importlib.util,
            "find_spec",
            lambda name: object() if name in {"fastembed", "sqlite_vec"} else None,
        )
        config = BasicMemoryConfig()
        assert config.semantic_search_enabled is True

    def test_semantic_search_enabled_defaults_to_false_when_any_semantic_module_is_unavailable(
        self, monkeypatch
    ):
        """Semantic search defaults off when required semantic modules are missing."""
        import basic_memory.config as config_module

        monkeypatch.delenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", raising=False)
        monkeypatch.setattr(
            config_module.importlib.util,
            "find_spec",
            lambda name: object() if name == "fastembed" else None,
        )
        config = BasicMemoryConfig()
        assert config.semantic_search_enabled is False

    def test_semantic_search_enabled_env_var_overrides_dependency_default(self, monkeypatch):
        """Environment overrides should win over dependency-based defaults."""
        import basic_memory.config as config_module

        monkeypatch.setattr(config_module.importlib.util, "find_spec", lambda name: None)

        monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "true")
        enabled = BasicMemoryConfig()
        assert enabled.semantic_search_enabled is True

        monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")
        disabled = BasicMemoryConfig()
        assert disabled.semantic_search_enabled is False

    def test_semantic_embedding_dimensions_defaults_to_none(self):
        """Dimensions should default to None, letting the provider choose."""
        config = BasicMemoryConfig()
        assert config.semantic_embedding_dimensions is None

    def test_semantic_embedding_dimensions_can_be_set(self):
        """Explicit dimensions should be stored on the config object."""
        config = BasicMemoryConfig(semantic_embedding_dimensions=1536)
        assert config.semantic_embedding_dimensions == 1536

    def test_semantic_search_enabled_description_mentions_both_backends(self):
        """Description should not say 'SQLite only' anymore."""
        field_info = BasicMemoryConfig.model_fields["semantic_search_enabled"]
        assert "SQLite only" not in (field_info.description or "")

    def test_semantic_min_similarity_defaults_to_055(self):
        """Threshold defaults to 0.55 to filter irrelevant vector results."""
        config = BasicMemoryConfig()
        assert config.semantic_min_similarity == 0.55

    def test_semantic_min_similarity_bounds_validation(self):
        """Threshold must be between 0.0 and 1.0."""
        config = BasicMemoryConfig(semantic_min_similarity=0.55)
        assert config.semantic_min_similarity == 0.55

        with pytest.raises(Exception):
            BasicMemoryConfig(semantic_min_similarity=-0.1)

        with pytest.raises(Exception):
            BasicMemoryConfig(semantic_min_similarity=1.1)


class TestFormattingConfig:
    """Test file formatting configuration options."""

    def test_format_on_save_defaults_to_false(self):
        """Test that format_on_save is disabled by default."""
        config = BasicMemoryConfig()
        assert config.format_on_save is False

    def test_format_on_save_can_be_enabled(self):
        """Test that format_on_save can be set to True."""
        config = BasicMemoryConfig(format_on_save=True)
        assert config.format_on_save is True

    def test_formatter_command_defaults_to_none(self):
        """Test that formatter_command defaults to None (uses built-in mdformat)."""
        config = BasicMemoryConfig()
        assert config.formatter_command is None

    def test_formatter_command_can_be_set(self):
        """Test that formatter_command can be configured."""
        config = BasicMemoryConfig(formatter_command="prettier --write {file}")
        assert config.formatter_command == "prettier --write {file}"

    def test_formatters_defaults_to_empty_dict(self):
        """Test that formatters defaults to empty dict."""
        config = BasicMemoryConfig()
        assert config.formatters == {}

    def test_formatters_can_be_configured(self):
        """Test that per-extension formatters can be configured."""
        config = BasicMemoryConfig(
            formatters={
                "md": "prettier --write {file}",
                "json": "jq . {file} > {file}.tmp && mv {file}.tmp {file}",
            }
        )
        assert config.formatters["md"] == "prettier --write {file}"
        assert "json" in config.formatters

    def test_formatter_timeout_defaults_to_5_seconds(self):
        """Test that formatter_timeout defaults to 5.0 seconds."""
        config = BasicMemoryConfig()
        assert config.formatter_timeout == 5.0

    def test_formatter_timeout_can_be_customized(self):
        """Test that formatter_timeout can be set to a different value."""
        config = BasicMemoryConfig(formatter_timeout=10.0)
        assert config.formatter_timeout == 10.0

    def test_formatter_timeout_must_be_positive(self):
        """Test that formatter_timeout validation rejects non-positive values."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            BasicMemoryConfig(formatter_timeout=0)

        with pytest.raises(pydantic.ValidationError):
            BasicMemoryConfig(formatter_timeout=-1)

    def test_formatting_env_vars(self, monkeypatch):
        """Test that formatting config can be set via environment variables."""
        monkeypatch.setenv("BASIC_MEMORY_FORMAT_ON_SAVE", "true")
        monkeypatch.setenv("BASIC_MEMORY_FORMATTER_COMMAND", "prettier --write {file}")
        monkeypatch.setenv("BASIC_MEMORY_FORMATTER_TIMEOUT", "10.0")

        config = BasicMemoryConfig()

        assert config.format_on_save is True
        assert config.formatter_command == "prettier --write {file}"
        assert config.formatter_timeout == 10.0

    def test_formatters_env_var_json(self, monkeypatch):
        """Test that formatters dict can be set via JSON environment variable."""
        import json

        formatters_json = json.dumps({"md": "prettier --write {file}", "json": "jq . {file}"})
        monkeypatch.setenv("BASIC_MEMORY_FORMATTERS", formatters_json)

        config = BasicMemoryConfig()

        assert config.formatters == {"md": "prettier --write {file}", "json": "jq . {file}"}

    def test_save_and_load_formatting_config(self):
        """Test that formatting config survives save/load cycle."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create config with formatting settings
            test_config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
                format_on_save=True,
                formatter_command="prettier --write {file}",
                formatters={"md": "prettier --write {file}", "json": "prettier --write {file}"},
                formatter_timeout=10.0,
            )
            config_manager.save_config(test_config)

            # Load and verify
            loaded_config = config_manager.load_config()
            assert loaded_config.format_on_save is True
            assert loaded_config.formatter_command == "prettier --write {file}"
            assert loaded_config.formatters == {
                "md": "prettier --write {file}",
                "json": "prettier --write {file}",
            }
            assert loaded_config.formatter_timeout == 10.0


class TestProjectMode:
    """Test per-project routing mode configuration."""

    def test_project_mode_defaults(self):
        """Test that ProjectMode enum has expected values."""
        assert ProjectMode.LOCAL.value == "local"
        assert ProjectMode.CLOUD.value == "cloud"

    def test_get_project_mode_defaults_to_local(self):
        """Test that unknown projects default to LOCAL mode."""
        config = BasicMemoryConfig()
        assert config.get_project_mode("nonexistent") == ProjectMode.LOCAL

    def test_set_project_mode_cloud(self):
        """Test setting a project to cloud mode."""
        config = BasicMemoryConfig()
        config.set_project_mode("research", ProjectMode.CLOUD)
        assert config.get_project_mode("research") == ProjectMode.CLOUD

    def test_set_project_mode_local_resets_to_default(self):
        """Test that setting a project back to LOCAL resets the entry's mode."""
        config = BasicMemoryConfig()
        # Need a project entry to set mode on
        config.projects["research"] = ProjectEntry(path="/tmp/research")
        config.set_project_mode("research", ProjectMode.CLOUD)
        assert config.projects["research"].mode == ProjectMode.CLOUD

        config.set_project_mode("research", ProjectMode.LOCAL)
        assert config.projects["research"].mode == ProjectMode.LOCAL
        assert config.get_project_mode("research") == ProjectMode.LOCAL

    def test_cloud_api_key_defaults_to_none(self):
        """Test that cloud_api_key defaults to None."""
        config = BasicMemoryConfig()
        assert config.cloud_api_key is None

    def test_cloud_api_key_can_be_set(self):
        """Test that cloud_api_key can be configured."""
        config = BasicMemoryConfig(cloud_api_key="bmc_test123")
        assert config.cloud_api_key == "bmc_test123"

    def test_project_mode_round_trip(self):
        """Test that project mode survives save/load cycle."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create config with project mode and cloud_api_key
            test_config = BasicMemoryConfig(
                projects={
                    "main": {"path": str(temp_path / "main")},
                    "research": {"path": str(temp_path / "research"), "mode": "cloud"},
                },
                cloud_api_key="bmc_test123",
            )
            config_manager.save_config(test_config)

            # Load and verify
            loaded = config_manager.load_config()
            assert loaded.cloud_api_key == "bmc_test123"
            assert loaded.get_project_mode("research") == ProjectMode.CLOUD
            assert loaded.get_project_mode("main") == ProjectMode.LOCAL

    def test_backward_compat_loading_old_format_without_project_modes(self):
        """Test that old config files with Dict[str, str] projects are migrated."""
        import json

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Write old-style config with Dict[str, str] projects
            old_config_data = {
                "env": "dev",
                "projects": {"main": str(temp_path / "main")},
                "default_project": "main",
                "log_level": "INFO",
            }
            config_manager.config_file.write_text(json.dumps(old_config_data, indent=2))

            # Clear config cache
            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None

            # Should load successfully with migration
            config = config_manager.load_config()
            assert config.cloud_api_key is None
            assert config.get_project_mode("main") == ProjectMode.LOCAL
            assert isinstance(config.projects["main"], ProjectEntry)

    def test_project_list_includes_mode(self, config_home):
        """Test that project_list property includes mode information."""
        config = BasicMemoryConfig(
            projects={
                "main": {"path": str(config_home / "main")},
                "research": {"path": str(config_home / "research"), "mode": "cloud"},
            },
        )

        project_list = config.project_list
        modes_by_name = {p.name: p.mode for p in project_list}
        assert modes_by_name["main"] == ProjectMode.LOCAL
        assert modes_by_name["research"] == ProjectMode.CLOUD
