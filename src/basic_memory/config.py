"""Configuration management for basic-memory."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, List, Tuple

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import basic_memory
from basic_memory.utils import setup_logging, generate_permalink


DATABASE_NAME = "memory.db"
APP_DATABASE_NAME = "memory.db"  # Using the same name but in the app directory
DATA_DIR_NAME = ".basic-memory"
CONFIG_FILE_NAME = "config.json"
WATCH_STATUS_JSON = "watch-status.json"

Environment = Literal["test", "dev", "user"]


@dataclass
class ProjectConfig:
    """Configuration for a specific basic-memory project."""

    name: str
    home: Path

    @property
    def project(self):
        return self.name

    @property
    def project_url(self) -> str:  # pragma: no cover
        return f"/{generate_permalink(self.name)}"


class BasicMemoryConfig(BaseSettings):
    """Pydantic model for Basic Memory global configuration."""

    env: Environment = Field(default="dev", description="Environment name")

    projects: Dict[str, str] = Field(
        default_factory=lambda: {
            "main": Path(os.getenv("BASIC_MEMORY_HOME", Path.home() / "basic-memory")).as_posix()
        },
        description="Mapping of project names to their filesystem paths",
    )
    default_project: str = Field(
        default="main",
        description="Name of the default project to use",
    )
    default_project_mode: bool = Field(
        default=False,
        description="When True, MCP tools automatically use default_project when no project parameter is specified. Enables simplified UX for single-project workflows.",
    )

    # overridden by ~/.basic-memory/config.json
    log_level: str = "INFO"

    # Watch service configuration
    sync_delay: int = Field(
        default=1000, description="Milliseconds to wait after changes before syncing", gt=0
    )

    watch_project_reload_interval: int = Field(
        default=30, description="Seconds between reloading project list in watch service", gt=0
    )

    # update permalinks on move
    update_permalinks_on_move: bool = Field(
        default=False,
        description="Whether to update permalinks when files are moved or renamed. default (False)",
    )

    sync_changes: bool = Field(
        default=True,
        description="Whether to sync changes in real time. default (True)",
    )

    sync_thread_pool_size: int = Field(
        default=4,
        description="Size of thread pool for file I/O operations in sync service",
        gt=0,
    )

    kebab_filenames: bool = Field(
        default=False,
        description="Format for generated filenames. False preserves spaces and special chars, True converts them to hyphens for consistency with permalinks",
    )

    disable_permalinks: bool = Field(
        default=False,
        description="Disable automatic permalink generation in frontmatter. When enabled, new notes won't have permalinks added and sync won't update permalinks. Existing permalinks will still work for reading.",
    )

    skip_initialization_sync: bool = Field(
        default=False,
        description="Skip expensive initialization synchronization. Useful for cloud/stateless deployments where project reconciliation is not needed.",
    )

    # Project path constraints
    project_root: Optional[str] = Field(
        default=None,
        description="If set, all projects must be created underneath this directory. Paths will be sanitized and constrained to this root. If not set, projects can be created anywhere (default behavior).",
    )

    # API connection configuration
    api_url: Optional[str] = Field(
        default=None,
        description="URL of remote Basic Memory API. If set, MCP will connect to this API instead of using local ASGI transport.",
    )

    # Cloud configuration
    cloud_client_id: str = Field(
        default="client_01K6KWQPW6J1M8VV7R3TZP5A6M",
        description="OAuth client ID for Basic Memory Cloud",
    )

    cloud_domain: str = Field(
        default="https://eloquent-lotus-05.authkit.app",
        description="AuthKit domain for Basic Memory Cloud",
    )

    cloud_host: str = Field(
        default_factory=lambda: os.getenv(
            "BASIC_MEMORY_CLOUD_HOST", "https://cloud.basicmemory.com"
        ),
        description="Basic Memory Cloud host URL",
    )

    cloud_mode: bool = Field(
        default=False,
        description="Enable cloud mode - all requests go to cloud instead of local (config file value)",
    )

    @property
    def cloud_mode_enabled(self) -> bool:
        """Check if cloud mode is enabled.

        Priority:
        1. BASIC_MEMORY_CLOUD_MODE environment variable
        2. Config file value (cloud_mode)
        """
        env_value = os.environ.get("BASIC_MEMORY_CLOUD_MODE", "").lower()
        if env_value in ("true", "1", "yes"):
            return True
        elif env_value in ("false", "0", "no"):
            return False
        # Fall back to config file value
        return self.cloud_mode

    bisync_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "profile": "balanced",
            "sync_dir": str(Path.home() / "basic-memory-cloud-sync"),
        },
        description="Bisync configuration for cloud sync",
    )

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",
        extra="ignore",
    )

    def get_project_path(self, project_name: Optional[str] = None) -> Path:  # pragma: no cover
        """Get the path for a specific project or the default project."""
        name = project_name or self.default_project

        if name not in self.projects:
            raise ValueError(f"Project '{name}' not found in configuration")

        return Path(self.projects[name])

    def model_post_init(self, __context: Any) -> None:
        """Ensure configuration is valid after initialization."""
        # Ensure main project exists
        if "main" not in self.projects:  # pragma: no cover
            self.projects["main"] = (
                Path(os.getenv("BASIC_MEMORY_HOME", Path.home() / "basic-memory"))
            ).as_posix()

        # Ensure default project is valid
        if self.default_project not in self.projects:  # pragma: no cover
            self.default_project = "main"

    @property
    def app_database_path(self) -> Path:
        """Get the path to the app-level database.

        This is the single database that will store all knowledge data
        across all projects.
        """
        database_path = Path.home() / DATA_DIR_NAME / APP_DATABASE_NAME
        if not database_path.exists():  # pragma: no cover
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.touch()
        return database_path

    @property
    def database_path(self) -> Path:
        """Get SQLite database path.

        Rreturns the app-level database path
        for backward compatibility in the codebase.
        """

        # Load the app-level database path from the global config
        config_manager = ConfigManager()
        config = config_manager.load_config()  # pragma: no cover
        return config.app_database_path  # pragma: no cover

    @property
    def project_list(self) -> List[ProjectConfig]:  # pragma: no cover
        """Get all configured projects as ProjectConfig objects."""
        return [ProjectConfig(name=name, home=Path(path)) for name, path in self.projects.items()]

    @field_validator("projects")
    @classmethod
    def ensure_project_paths_exists(cls, v: Dict[str, str]) -> Dict[str, str]:  # pragma: no cover
        """Ensure project path exists."""
        for name, path_value in v.items():
            path = Path(path_value)
            if not Path(path).exists():
                try:
                    path.mkdir(parents=True)
                except Exception as e:
                    logger.error(f"Failed to create project path: {e}")
                    raise e
        return v

    @property
    def data_dir_path(self):
        return Path.home() / DATA_DIR_NAME


# Module-level cache for configuration
_CONFIG_CACHE: Optional[BasicMemoryConfig] = None


class ConfigManager:
    """Manages Basic Memory configuration."""

    def __init__(self) -> None:
        """Initialize the configuration manager."""
        home = os.getenv("HOME", Path.home())
        if isinstance(home, str):
            home = Path(home)

        # Allow override via environment variable
        if config_dir := os.getenv("BASIC_MEMORY_CONFIG_DIR"):
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = home / DATA_DIR_NAME

        self.config_file = self.config_dir / CONFIG_FILE_NAME

        # Ensure config directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def config(self) -> BasicMemoryConfig:
        """Get configuration, loading it lazily if needed."""
        return self.load_config()

    def load_config(self) -> BasicMemoryConfig:
        """Load configuration from file or create default.

        Environment variables take precedence over file config values,
        following Pydantic Settings best practices.

        Uses module-level cache for performance across ConfigManager instances.
        """
        global _CONFIG_CACHE

        # Return cached config if available
        if _CONFIG_CACHE is not None:
            return _CONFIG_CACHE

        if self.config_file.exists():
            try:
                file_data = json.loads(self.config_file.read_text(encoding="utf-8"))

                # First, create config from environment variables (Pydantic will read them)
                # Then overlay with file data for fields that aren't set via env vars
                # This ensures env vars take precedence

                # Get env-based config fields that are actually set
                env_config = BasicMemoryConfig()
                env_dict = env_config.model_dump()

                # Merge: file data as base, but only use it for fields not set by env
                # We detect env-set fields by comparing to default values
                merged_data = file_data.copy()

                # For fields that have env var overrides, use those instead of file values
                # The env_prefix is "BASIC_MEMORY_" so we check those
                for field_name in BasicMemoryConfig.model_fields.keys():
                    env_var_name = f"BASIC_MEMORY_{field_name.upper()}"
                    if env_var_name in os.environ:
                        # Environment variable is set, use it
                        merged_data[field_name] = env_dict[field_name]

                _CONFIG_CACHE = BasicMemoryConfig(**merged_data)
                return _CONFIG_CACHE
            except Exception as e:  # pragma: no cover
                logger.exception(f"Failed to load config: {e}")
                raise e
        else:
            config = BasicMemoryConfig()
            self.save_config(config)
            return config

    def save_config(self, config: BasicMemoryConfig) -> None:
        """Save configuration to file and invalidate cache."""
        global _CONFIG_CACHE
        save_basic_memory_config(self.config_file, config)
        # Invalidate cache so next load_config() reads fresh data
        _CONFIG_CACHE = None

    @property
    def projects(self) -> Dict[str, str]:
        """Get all configured projects."""
        return self.config.projects.copy()

    @property
    def default_project(self) -> str:
        """Get the default project name."""
        return self.config.default_project

    def add_project(self, name: str, path: str) -> ProjectConfig:
        """Add a new project to the configuration."""
        project_name, _ = self.get_project(name)
        if project_name:  # pragma: no cover
            raise ValueError(f"Project '{name}' already exists")

        # Ensure the path exists
        project_path = Path(path)
        project_path.mkdir(parents=True, exist_ok=True)  # pragma: no cover

        # Load config, modify it, and save it
        config = self.load_config()
        config.projects[name] = project_path.as_posix()
        self.save_config(config)
        return ProjectConfig(name=name, home=project_path)

    def remove_project(self, name: str) -> None:
        """Remove a project from the configuration."""

        project_name, path = self.get_project(name)
        if not project_name:  # pragma: no cover
            raise ValueError(f"Project '{name}' not found")

        # Load config, check, modify, and save
        config = self.load_config()
        if project_name == config.default_project:  # pragma: no cover
            raise ValueError(f"Cannot remove the default project '{name}'")

        del config.projects[name]
        self.save_config(config)

    def set_default_project(self, name: str) -> None:
        """Set the default project."""
        project_name, path = self.get_project(name)
        if not project_name:  # pragma: no cover
            raise ValueError(f"Project '{name}' not found")

        # Load config, modify, and save
        config = self.load_config()
        config.default_project = project_name
        self.save_config(config)

    def get_project(self, name: str) -> Tuple[str, str] | Tuple[None, None]:
        """Look up a project from the configuration by name or permalink"""
        project_permalink = generate_permalink(name)
        app_config = self.config
        for project_name, path in app_config.projects.items():
            if project_permalink == generate_permalink(project_name):
                return project_name, path
        return None, None


def get_project_config(project_name: Optional[str] = None) -> ProjectConfig:
    """
    Get the project configuration for the current session.
    If project_name is provided, it will be used instead of the default project.
    """

    actual_project_name = None

    # load the config from file
    config_manager = ConfigManager()
    app_config = config_manager.load_config()

    # Get project name from environment variable
    os_project_name = os.environ.get("BASIC_MEMORY_PROJECT", None)
    if os_project_name:  # pragma: no cover
        logger.warning(
            f"BASIC_MEMORY_PROJECT is not supported anymore. Set the default project in the config instead. Setting default project to {os_project_name}"
        )
        actual_project_name = project_name
    # if the project_name is passed in, use it
    elif not project_name:
        # use default
        actual_project_name = app_config.default_project
    else:  # pragma: no cover
        actual_project_name = project_name

    # the config contains a dict[str,str] of project names and absolute paths
    assert actual_project_name is not None, "actual_project_name cannot be None"

    project_permalink = generate_permalink(actual_project_name)

    for name, path in app_config.projects.items():
        if project_permalink == generate_permalink(name):
            return ProjectConfig(name=name, home=Path(path))

    # otherwise raise error
    raise ValueError(f"Project '{actual_project_name}' not found")  # pragma: no cover


def save_basic_memory_config(file_path: Path, config: BasicMemoryConfig) -> None:
    """Save configuration to file."""
    try:
        file_path.write_text(json.dumps(config.model_dump(), indent=2))
    except Exception as e:  # pragma: no cover
        logger.error(f"Failed to save config: {e}")


# setup logging to a single log file in user home directory
user_home = Path.home()
log_dir = user_home / DATA_DIR_NAME
log_dir.mkdir(parents=True, exist_ok=True)


# Process info for logging
def get_process_name():  # pragma: no cover
    """
    get the type of process for logging
    """
    import sys

    if "sync" in sys.argv:
        return "sync"
    elif "mcp" in sys.argv:
        return "mcp"
    elif "cli" in sys.argv:
        return "cli"
    else:
        return "api"


process_name = get_process_name()

# Global flag to track if logging has been set up
_LOGGING_SETUP = False


# Logging


def setup_basic_memory_logging():  # pragma: no cover
    """Set up logging for basic-memory, ensuring it only happens once."""
    global _LOGGING_SETUP
    if _LOGGING_SETUP:
        # We can't log before logging is set up
        # print("Skipping duplicate logging setup")
        return

    # Check for console logging environment variable - accept more truthy values
    console_logging_env = os.getenv("BASIC_MEMORY_CONSOLE_LOGGING", "false").lower()
    console_logging = console_logging_env in ("true", "1", "yes", "on")

    # Check for log level environment variable first, fall back to config
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL")
    if not log_level:
        config_manager = ConfigManager()
        log_level = config_manager.config.log_level

    config_manager = ConfigManager()
    config = get_project_config()
    setup_logging(
        env=config_manager.config.env,
        home_dir=user_home,  # Use user home for logs
        log_level=log_level,
        log_file=f"{DATA_DIR_NAME}/basic-memory-{process_name}.log",
        console=console_logging,
    )

    logger.info(f"Basic Memory {basic_memory.__version__} (Project: {config.project})")
    _LOGGING_SETUP = True


# Set up logging
setup_basic_memory_logging()
