"""Configuration management for basic-memory."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import basic_memory
from basic_memory.utils import setup_logging

DATABASE_NAME = "memory.db"
DATA_DIR_NAME = ".basic-memory"
CONFIG_FILE_NAME = "config.json"

Environment = Literal["test", "dev", "user"]


class ProjectConfig(BaseSettings):
    """Configuration for a specific basic-memory project."""

    env: Environment = Field(default="dev", description="Environment name")

    # Default to ~/basic-memory but allow override with env var: BASIC_MEMORY_HOME
    home: Path = Field(
        default_factory=lambda: Path.home() / "basic-memory",
        description="Base path for basic-memory files",
    )

    # Name of the project
    project: str = Field(default="default", description="Project name")

    # Watch service configuration
    sync_delay: int = Field(
        default=500, description="Milliseconds to wait after changes before syncing", gt=0
    )

    log_level: str = "DEBUG"

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @property
    def database_path(self) -> Path:
        """Get SQLite database path."""
        database_path = self.home / DATA_DIR_NAME / DATABASE_NAME
        if not database_path.exists():
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.touch()
        return database_path

    @field_validator("home")
    @classmethod
    def ensure_path_exists(cls, v: Path) -> Path:  # pragma: no cover
        """Ensure project path exists."""
        if not v.exists():
            v.mkdir(parents=True)
        return v


class BasicMemoryConfig(BaseSettings):
    """Pydantic model for Basic Memory global configuration."""

    projects: Dict[str, str] = Field(
        default_factory=lambda: {"main": str(Path.home() / "basic-memory")},
        description="Mapping of project names to their filesystem paths",
    )
    default_project: str = Field(
        default="main",
        description="Name of the default project to use",
    )

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",
        extra="ignore",
    )

    def model_post_init(self, __context: Any) -> None:
        """Ensure configuration is valid after initialization."""
        # Ensure main project exists
        if "main" not in self.projects:
            self.projects["main"] = str(Path.home() / "basic-memory")

        # Ensure default project is valid
        if self.default_project not in self.projects:
            self.default_project = "main"


class ConfigManager:
    """Manages Basic Memory configuration."""

    def __init__(self) -> None:
        """Initialize the configuration manager."""
        self.config_dir = Path.home() / DATA_DIR_NAME
        self.config_file = self.config_dir / CONFIG_FILE_NAME

        # Ensure config directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Load or create configuration
        self.config = self.load_config()

    def load_config(self) -> BasicMemoryConfig:
        """Load configuration from file or create default."""
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text())
                return BasicMemoryConfig(**data)
            except Exception as e:
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

    def get_project_path(self, project_name: Optional[str] = None) -> Path:
        """Get the path for a specific project or the default project."""
        name = project_name or self.config.default_project

        # Check if specified in environment variable
        if not project_name and "BASIC_MEMORY_PROJECT" in os.environ:
            name = os.environ["BASIC_MEMORY_PROJECT"]

        if name not in self.config.projects:
            raise ValueError(f"Project '{name}' not found in configuration")

        return Path(self.config.projects[name])

    def add_project(self, name: str, path: str) -> None:
        """Add a new project to the configuration."""
        if name in self.config.projects:
            raise ValueError(f"Project '{name}' already exists")

        # Ensure the path exists
        project_path = Path(path)
        project_path.mkdir(parents=True, exist_ok=True)

        self.config.projects[name] = str(project_path)
        self.save_config(self.config)

    def remove_project(self, name: str) -> None:
        """Remove a project from the configuration."""
        if name not in self.config.projects:
            raise ValueError(f"Project '{name}' not found")

        if name == self.config.default_project:
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
    """Get a project configuration for the specified project."""
    config_manager = ConfigManager()

    # Get project name from environment variable or use provided name or default
    actual_project_name = os.environ.get(
        "BASIC_MEMORY_PROJECT", project_name or config_manager.default_project
    )

    try:
        project_path = config_manager.get_project_path(actual_project_name)
        return ProjectConfig(home=project_path, project=actual_project_name)
    except ValueError:  # pragma: no cover
        logger.warning(f"Project '{actual_project_name}' not found, using default")
        project_path = config_manager.get_project_path(config_manager.default_project)
        return ProjectConfig(home=project_path, project=config_manager.default_project)


# Create config manager
config_manager = ConfigManager()

# Load project config for current context
config = get_project_config()

# setup logging to a single log file in user home directory
user_home = Path.home()
log_dir = user_home / DATA_DIR_NAME
log_dir.mkdir(parents=True, exist_ok=True)

setup_logging(
    env=config.env,
    home_dir=user_home,  # Use user home for logs
    log_level=config.log_level,
    log_file=f"{DATA_DIR_NAME}/basic-memory.log",
    console=False,
)
logger.info(f"Starting Basic Memory {basic_memory.__version__} (Project: {config.project})")
