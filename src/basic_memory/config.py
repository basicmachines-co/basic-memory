"""Configuration management for basic-memory."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, List

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from setuptools.command.setopt import config_file

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
        default_factory=lambda: {"main": str(Path.home() / "basic-memory")},
        description="Mapping of project names to their filesystem paths",
    )
    default_project: str = Field(
        default="main",
        description="Name of the default project to use",
    )

    # overridden by ~/.basic-memory/config.json
    log_level: str = "INFO"

    # Watch service configuration
    sync_delay: int = Field(
        default=1000, description="Milliseconds to wait after changes before syncing", gt=0
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

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
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
            self.projects["main"] = str(Path.home() / "basic-memory")

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


class ConfigManager:
    """Manages Basic Memory configuration."""

    def __init__(self) -> None:
        """Initialize the configuration manager."""
        home = os.getenv("HOME", Path.home())
        if isinstance(home, str):
            home = Path(home)

        self.config_dir = home / DATA_DIR_NAME
        self.config_file = self.config_dir / CONFIG_FILE_NAME

        # Ensure config directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Load or create configuration
        self.config = self.load_config()

    def load_config(self) -> BasicMemoryConfig:
        """Load configuration from file or create default."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                return BasicMemoryConfig(**data)
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed to load config: {e}")
                config = BasicMemoryConfig()
                self.save_config(config)
                return config
        else:
            config = BasicMemoryConfig()
            self.save_config(config)
            return config

    def save_config(self, config: BasicMemoryConfig) -> None:
        """Save configuration to file."""
        try:            
            self.config_file.write_text(json.dumps(config.model_dump(), indent=2))
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to save config: {e}")

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
        if name in self.config.projects:  # pragma: no cover
            raise ValueError(f"Project '{name}' already exists")

        # Ensure the path exists
        project_path = Path(path)
        project_path.mkdir(parents=True, exist_ok=True)  # pragma: no cover

        self.config.projects[name] = str(project_path)
        self.save_config(self.config)
        return ProjectConfig(name=name, home=project_path)

    def remove_project(self, name: str) -> None:
        """Remove a project from the configuration."""
        if name not in self.config.projects:  # pragma: no cover
            raise ValueError(f"Project '{name}' not found")

        if name == self.config.default_project:  # pragma: no cover
            raise ValueError(f"Cannot remove the default project '{name}'")

        del self.config.projects[name]
        self.save_config(self.config)

    def set_default_project(self, name: str) -> None:
        """Set the default project."""
        if name not in self.config.projects:  # pragma: no cover
            raise ValueError(f"Project '{name}' not found")

        self.config.default_project = name
        self.save_config(self.config)


def get_project_config(project_name: Optional[str] = None) -> ProjectConfig:
    """
    Get the project configuration for the current session.
    If project_name is provided, it will be used instead of the default project.
    """

    actual_project_name = None

    # load the config from file
    global app_config
    app_config = config_manager.load_config()

    # Get project name from environment variable
    os_project_name = os.environ.get("BASIC_MEMORY_PROJECT", None)
    if os_project_name:  # pragma: no cover
        logger.warning(
            f"BASIC_MEMORY_PROJECT is not supported anymore. Use the --project flag or set the default project in the config instead. Setting default project to {os_project_name}"
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

    project_path = app_config.projects.get(actual_project_name)
    if not project_path:  # pragma: no cover
        raise ValueError(f"Project '{actual_project_name}' not found")

    return ProjectConfig(name=actual_project_name, home=Path(project_path))


# Create config manager
config_manager = ConfigManager()

# Export the app-level config
app_config: BasicMemoryConfig = config_manager.config

# Load project config for the default project (backward compatibility)
config: ProjectConfig = get_project_config()


def update_current_project(project_name: str) -> None:
    """Update the global config to use a different project.

    This is used by the CLI when --project flag is specified.
    """
    global config
    config = get_project_config(project_name)  # pragma: no cover


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

    setup_logging(
        env=config_manager.config.env,
        home_dir=user_home,  # Use user home for logs
        log_level=config_manager.load_config().log_level,
        log_file=f"{DATA_DIR_NAME}/basic-memory-{process_name}.log",
        console=False,
    )

    logger.info(f"Basic Memory {basic_memory.__version__} (Project: {config.project})")
    _LOGGING_SETUP = True


# Set up logging
setup_basic_memory_logging()
