"""Configuration persistence and the stable public configuration facade."""

import importlib as importlib
import importlib.util  # noqa: F401 - preserves the historical config.importlib.util seam
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from loguru import logger

from basic_memory import __version__
from basic_memory import config_logging as _config_logging
from basic_memory.config_models import (
    APP_DATABASE_NAME as APP_DATABASE_NAME,
    CONFIG_DIR_MODE as CONFIG_DIR_MODE,
    CONFIG_FILE_MODE as CONFIG_FILE_MODE,
    CONFIG_FILE_NAME as CONFIG_FILE_NAME,
    DATABASE_NAME as DATABASE_NAME,
    DATA_DIR_NAME as DATA_DIR_NAME,
    WATCH_STATUS_JSON as WATCH_STATUS_JSON,
    BasicMemoryConfig as BasicMemoryConfig,
    CloudProjectConfig as CloudProjectConfig,
    DatabaseBackend as DatabaseBackend,
    Environment as Environment,
    ProjectConfig as ProjectConfig,
    ProjectEntry as ProjectEntry,
    ProjectMode as ProjectMode,
    _secure_config_dir,
    _secure_config_file,
    default_fastembed_cache_dir as default_fastembed_cache_dir,
    resolve_data_dir as resolve_data_dir,
)
from basic_memory.telemetry import configure_telemetry
from basic_memory.utils import generate_permalink, setup_logging


# Cache state remains on the public module because long-lived callers and test
# fixtures deliberately reset these names between isolated config directories.
_CONFIG_CACHE: Optional[BasicMemoryConfig] = None
_CONFIG_MTIME: Optional[float] = None
_CONFIG_SIZE: Optional[int] = None


class ConfigManager:
    """Manage Basic Memory's persisted global configuration."""

    def __init__(self) -> None:
        self.config_dir = resolve_data_dir()
        self.config_file = self.config_dir / CONFIG_FILE_NAME
        self.config_dir.mkdir(parents=True, exist_ok=True)
        _secure_config_dir(self.config_dir)

    @property
    def config(self) -> BasicMemoryConfig:
        """Get configuration, loading it lazily if needed."""
        return self.load_config()

    def load_config(self) -> BasicMemoryConfig:
        """Load configuration with environment values taking file precedence."""
        global _CONFIG_CACHE, _CONFIG_MTIME, _CONFIG_SIZE

        if _CONFIG_CACHE is not None:
            try:
                stat_result = self.config_file.stat()
                current_mtime = stat_result.st_mtime
                current_size = stat_result.st_size
            except OSError:
                current_mtime = None
                current_size = None

            if (
                current_mtime is not None
                and current_mtime == _CONFIG_MTIME
                and current_size == _CONFIG_SIZE
            ):
                return _CONFIG_CACHE

            _CONFIG_CACHE = None
            _CONFIG_MTIME = None
            _CONFIG_SIZE = None

        if self.config_file.exists():
            try:
                file_data = json.loads(self.config_file.read_text(encoding="utf-8"))
                stale_keys = {
                    "default_project_mode",
                    "project_modes",
                    "cloud_projects",
                    "cloud_mode",
                }
                needs_resave = bool(stale_keys & file_data.keys())

                projects_raw = file_data.get("projects", {})
                if projects_raw:
                    first_value = next(iter(projects_raw.values()), None)
                    if isinstance(first_value, str):
                        needs_resave = True

                if not needs_resave:
                    for entry_data in projects_raw.values():
                        if isinstance(entry_data, dict):
                            local_sync_path = entry_data.get("local_sync_path")
                            path = entry_data.get("path", "")
                            if local_sync_path and not os.path.isabs(path):
                                needs_resave = True
                                break

                merged_data = file_data.copy()
                for field_name in BasicMemoryConfig.model_fields:
                    env_var_name = f"BASIC_MEMORY_{field_name.upper()}"
                    if env_var_name in os.environ:
                        # BaseSettings only applies env precedence when the field
                        # is absent from constructor data.
                        merged_data.pop(field_name, None)

                _CONFIG_CACHE = BasicMemoryConfig(**merged_data)

                try:
                    stat_result = self.config_file.stat()
                    _CONFIG_MTIME = stat_result.st_mtime
                    _CONFIG_SIZE = stat_result.st_size
                except OSError:
                    _CONFIG_MTIME = None
                    _CONFIG_SIZE = None

                if needs_resave:
                    backup_path = self.config_file.with_suffix(".json.bak")
                    shutil.copy2(self.config_file, backup_path)
                    _secure_config_file(backup_path)
                    logger.info(f"Migrating config to current format (backup: {backup_path})")
                    save_basic_memory_config(self.config_file, _CONFIG_CACHE)

                return _CONFIG_CACHE
            except json.JSONDecodeError as error:  # pragma: no cover
                logger.error(f"Invalid JSON in config file {self.config_file}: {error}")
                raise SystemExit(
                    f"Error: config file is not valid JSON: {self.config_file}\n"
                    f"  {error}\n"
                    f"Fix or delete the file and re-run."
                )
            except Exception as error:  # pragma: no cover
                logger.error(f"Failed to load config from {self.config_file}: {error}")
                raise SystemExit(
                    f"Error: failed to load config from {self.config_file}\n"
                    f"  {error}\n"
                    f"Fix or delete the file and re-run."
                )

        config = BasicMemoryConfig()
        self.save_config(config)
        return config

    def save_config(self, config: BasicMemoryConfig) -> None:
        """Save configuration to file and invalidate the process cache."""
        global _CONFIG_CACHE, _CONFIG_MTIME, _CONFIG_SIZE
        save_basic_memory_config(self.config_file, config)
        _CONFIG_CACHE = None
        _CONFIG_MTIME = None
        _CONFIG_SIZE = None

    @property
    def projects(self) -> Dict[str, str]:
        """Return the legacy name-to-path project mapping."""
        return {name: entry.path for name, entry in self.config.projects.items()}

    @property
    def default_project(self) -> Optional[str]:
        return self.config.default_project

    def add_project(self, name: str, path: str) -> ProjectConfig:
        project_name, _ = self.get_project(name)
        if project_name:  # pragma: no cover
            raise ValueError(f"Project '{name}' already exists")

        project_path = Path(path)
        config = self.load_config()
        config.projects[name] = ProjectEntry(path=str(project_path))
        self.save_config(config)
        return ProjectConfig(name=name, home=project_path)

    def remove_project(self, name: str) -> None:
        project_name, _ = self.get_project(name)
        if not project_name:  # pragma: no cover
            raise ValueError(f"Project '{name}' not found")

        config = self.load_config()
        if project_name == config.default_project:  # pragma: no cover
            raise ValueError(f"Cannot remove the default project '{name}'")

        del config.projects[project_name]
        self.save_config(config)

    def set_default_project(self, name: str) -> None:
        project_name, _ = self.get_project(name)
        if not project_name:  # pragma: no cover
            raise ValueError(f"Project '{name}' not found")

        config = self.load_config()
        config.default_project = project_name
        self.save_config(config)

    def get_project(self, name: str) -> Tuple[str, str] | Tuple[None, None]:
        """Look up a project by display name or permalink."""
        project_permalink = generate_permalink(name)
        for project_name, entry in self.config.projects.items():
            if project_permalink == generate_permalink(project_name):
                return project_name, entry.path
        return None, None


def get_project_config(project_name: Optional[str] = None) -> ProjectConfig:
    """Get the requested or default project configuration."""
    actual_project_name = None
    app_config = ConfigManager().load_config()

    os_project_name = os.environ.get("BASIC_MEMORY_PROJECT")
    if os_project_name:  # pragma: no cover
        logger.warning(
            "BASIC_MEMORY_PROJECT is not supported anymore. Set the default project "
            f"in the config instead. Setting default project to {os_project_name}"
        )
        actual_project_name = project_name
    elif not project_name:
        actual_project_name = app_config.default_project
    else:  # pragma: no cover
        actual_project_name = project_name

    assert actual_project_name is not None, "actual_project_name cannot be None"
    project_permalink = generate_permalink(actual_project_name)
    for name, entry in app_config.projects.items():
        if project_permalink == generate_permalink(name):
            return ProjectConfig(name=name, home=Path(entry.path))

    raise ValueError(f"Project '{actual_project_name}' not found")  # pragma: no cover


def has_cloud_credentials(config: BasicMemoryConfig) -> bool:
    """Return whether API-key or OAuth cloud credentials are available."""
    if config.cloud_api_key:
        return True
    from basic_memory.cli.auth import CLIAuth

    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    return auth.load_tokens() is not None


def save_basic_memory_config(file_path: Path, config: BasicMemoryConfig) -> None:
    """Atomically save configuration so concurrent readers see complete JSON."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        _secure_config_dir(file_path.parent)
        config_dict = config.model_dump(mode="json")
        temp_path = file_path.parent / f"{file_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            temp_path.write_text(json.dumps(config_dict, indent=2))
            _secure_config_file(temp_path)
            os.replace(temp_path, file_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception as error:  # pragma: no cover
        logger.error(f"Failed to save config: {error}")


def _configure_logfire_for_entrypoint(entrypoint: str) -> None:
    _config_logging.configure_logfire_for_entrypoint(
        entrypoint,
        config=ConfigManager().config,
        service_version=__version__,
        configure_telemetry=configure_telemetry,
    )


def init_cli_logging() -> None:
    """Initialize CLI logging without writing protocol output to stdout."""
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL", "INFO")
    _configure_logfire_for_entrypoint("cli")
    _config_logging.initialize_file_logging(
        log_level=log_level,
        setup_logging=setup_logging,
    )


def init_mcp_logging() -> None:
    """Initialize MCP logging without corrupting the JSON-RPC stream."""
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL", "INFO")
    _configure_logfire_for_entrypoint("mcp")
    _config_logging.initialize_file_logging(
        log_level=log_level,
        setup_logging=setup_logging,
    )


def init_api_logging() -> None:
    """Initialize local file logging or structured Cloud stderr logging."""
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL", "INFO")
    _configure_logfire_for_entrypoint("api")
    cloud_mode = os.getenv("BASIC_MEMORY_CLOUD_MODE", "").lower() in ("1", "true")
    _config_logging.initialize_api_logging(
        log_level=log_level,
        cloud_mode=cloud_mode,
        setup_logging=setup_logging,
    )
