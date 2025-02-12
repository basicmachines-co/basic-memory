"""Configuration management for basic-memory."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_NAME = "memory.db"
DATA_DIR_NAME = ".basic-memory"


class ProjectConfig(BaseSettings):
    """Configuration for a specific basic-memory project."""

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

    log_level: str = "INFO"

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


# Load project config
config = ProjectConfig()
