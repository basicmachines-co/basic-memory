"""Configuration management for basic-memory."""

import importlib.util
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Literal, Optional, List

from loguru import logger
from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from basic_memory.config_migrations import (
    migrate_legacy_projects,
    migrate_legacy_sync_fields,
)
from basic_memory.utils import generate_permalink


DATABASE_NAME = "memory.db"
APP_DATABASE_NAME = "memory.db"  # Using the same name but in the app directory
DATA_DIR_NAME = "basic-memory"
CONFIG_FILE_NAME = "config.json"
WATCH_STATUS_JSON = "watch-status.json"
CONFIG_DIR_MODE = 0o700
CONFIG_FILE_MODE = 0o600

Environment = Literal["test", "dev", "user"]


def _secure_config_dir(path: Path) -> None:
    """Restrict config directory permissions on platforms with POSIX modes."""
    if os.name != "nt":
        path.chmod(CONFIG_DIR_MODE)


def _secure_config_file(path: Path) -> None:
    """Restrict config file permissions because config can contain cloud credentials."""
    if os.name != "nt":
        path.chmod(CONFIG_FILE_MODE)


class ProjectMode(str, Enum):
    """Per-project routing mode."""

    LOCAL = "local"
    CLOUD = "cloud"


class DatabaseBackend(str, Enum):
    """Supported database backends."""

    SQLITE = "sqlite"
    POSTGRES = "postgres"


def _default_semantic_search_enabled() -> bool:
    """Enable semantic search by default when required local semantic dependencies exist."""
    required_modules = ("fastembed", "sqlite_vec")
    return all(
        importlib.util.find_spec(module_name) is not None for module_name in required_modules
    )


def resolve_data_dir() -> Path:
    """Resolve the Basic Memory data directory.

    Single source of truth for the per-user state directory. Honors
    ``BASIC_MEMORY_CONFIG_DIR`` so each process/worktree can isolate config
    and database state; otherwise falls back to ``<user home>/.basic-memory``,
    and then to ``XDG_CONFIG_HOME``.

    Cross-platform: ``Path.home()`` reads ``$HOME`` on POSIX and
    ``%USERPROFILE%`` on Windows, so there's no need to check ``$HOME``
    explicitly here.
    """
    if basic_memory_dir := os.getenv("BASIC_MEMORY_CONFIG_DIR"):
        return Path(basic_memory_dir)
    if xdg_config := os.getenv("XDG_CONFIG_HOME"):
        return Path(xdg_config) / DATA_DIR_NAME
    return Path.home() / ("." + DATA_DIR_NAME)


def default_fastembed_cache_dir() -> str:
    """Return the default cache directory used for FastEmbed model artifacts.

    Resolution order:
      1. ``FASTEMBED_CACHE_PATH`` env var — honors FastEmbed's own convention
         so users who already configure it through the environment keep working.
      2. ``<basic-memory data dir>/fastembed_cache`` — the same stable,
         user-writable directory Basic Memory already uses for config and
         the default SQLite database. Honors ``BASIC_MEMORY_CONFIG_DIR``.

    Why not ``tempfile.gettempdir()``?
      FastEmbed's own default is ``<system tmp>/fastembed_cache``, which is
      ephemeral in many sandboxed MCP runtimes (e.g. Codex CLI wipes /tmp
      between invocations). The model then disappears and every subsequent
      ONNX load raises ``NO_SUCHFILE``. Persisting the cache under the
      per-user data directory works identically on macOS, Linux, and Windows.
    """
    if env_override := os.getenv("FASTEMBED_CACHE_PATH"):
        return env_override
    return str(resolve_data_dir() / "fastembed_cache")


@dataclass
class ProjectConfig:
    """Configuration for a specific basic-memory project."""

    name: str
    home: Path
    mode: ProjectMode = ProjectMode.LOCAL

    @property
    def project(self):
        return self.name  # pragma: no cover

    @property
    def project_url(self) -> str:  # pragma: no cover
        return f"/{generate_permalink(self.name)}"


class CloudProjectConfig(BaseModel):
    """Sync configuration for a cloud project.

    This tracks the local working directory and sync state for a project
    that is synced with Basic Memory Cloud.

    DEPRECATED: Kept for backward-compatible migration only. New code should
    use ProjectEntry fields (cloud_sync_path, bisync_initialized, last_sync).
    """

    local_path: str = Field(description="Local working directory path for this cloud project")
    last_sync: Optional[datetime] = Field(
        default=None, description="Timestamp of last successful sync operation"
    )
    bisync_initialized: bool = Field(
        default=False, description="Whether rclone bisync baseline has been established"
    )


class ProjectEntry(BaseModel):
    """Unified project configuration entry.

    Replaces the old triple of projects (Dict[str, str]), project_modes
    (Dict[str, ProjectMode]), and cloud_projects (Dict[str, CloudProjectConfig])
    with a single structure per project.
    """

    path: str = Field(description="Local filesystem path for the project")
    mode: ProjectMode = Field(
        default=ProjectMode.LOCAL,
        description="Routing mode: local (in-process ASGI) or cloud (remote API)",
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Cloud workspace tenant_id. Set by 'bm project set-cloud --workspace'.",
    )
    # Cloud sync state (replaces CloudProjectConfig)
    local_sync_path: Optional[str] = Field(
        default=None,
        description="Local working directory for bisync",
        validation_alias=AliasChoices("local_sync_path", "cloud_sync_path"),
    )
    bisync_initialized: bool = Field(
        default=False,
        description="Whether rclone bisync baseline has been established",
    )
    last_sync: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last successful sync operation",
    )


class BasicMemoryConfig(BaseSettings):
    """Pydantic model for Basic Memory global configuration."""

    if TYPE_CHECKING:
        # Pydantic accepts raw constructor data and validates/coerces it at runtime.
        # Model attributes remain strongly typed after initialization.
        def __init__(self, **data: Any) -> None: ...

    env: Environment = Field(default="dev", description="Environment name")

    projects: Dict[str, ProjectEntry] = Field(
        default_factory=lambda: (
            {
                "main": ProjectEntry(
                    path=str(Path(os.getenv("BASIC_MEMORY_HOME", Path.home() / "basic-memory")))
                )
            }
            if os.getenv("BASIC_MEMORY_HOME")
            else {}
        ),
        description="Mapping of project names to their ProjectEntry configuration",
    )
    default_project: Optional[str] = Field(
        default=None,
        description="Name of the default project to use. When set, acts as fallback when no project parameter is specified. Set to null to disable automatic project resolution.",
    )

    # overridden by ~/.basic-memory/config.json
    log_level: str = "INFO"

    # Optional Logfire telemetry (disabled by default)
    logfire_enabled: bool = Field(
        default=False,
        description="Enable Logfire instrumentation for local development or managed deployments.",
    )
    logfire_send_to_logfire: bool = Field(
        default=False,
        description="When true, allow Logfire to export telemetry to the configured backend.",
    )
    logfire_service_name: str = Field(
        default="basic-memory",
        description="Base service name used when constructing entrypoint-specific Logfire service names.",
    )
    logfire_environment: str | None = Field(
        default=None,
        description="Optional override for Logfire environment. Defaults to env when unset.",
    )

    # Database configuration
    database_backend: DatabaseBackend = Field(
        default=DatabaseBackend.SQLITE,
        description="Database backend to use (sqlite or postgres)",
    )

    database_url: Optional[str] = Field(
        default=None,
        description="Database connection URL. For Postgres, use postgresql+asyncpg://user:pass@host:port/db. If not set, SQLite will use default path.",
    )

    # Semantic search configuration
    semantic_search_enabled: bool = Field(
        default_factory=_default_semantic_search_enabled,
        description="Enable semantic search (vector/hybrid retrieval). Works on both SQLite and Postgres backends. Requires semantic dependencies (included by default).",
    )
    semantic_vector_index: str = Field(
        default="pgvector",
        description=(
            "Semantic vector index backend for Postgres deployments. 'pgvector' is built in; "
            "other names resolve through the basic_memory.semantic_vector_indexes entry-point "
            "group. SQLite continues to use sqlite-vec."
        ),
        min_length=1,
    )
    semantic_embedding_provider: str = Field(
        default="fastembed",
        description="Embedding provider for local semantic indexing/search.",
    )
    semantic_embedding_model: str = Field(
        default="bge-small-en-v1.5",
        description="Embedding model identifier used by the local provider.",
    )
    semantic_embedding_api_base: str | None = Field(
        default=None,
        description=(
            "Optional custom API base URL for the LiteLLM embedding provider. "
            "Use this for OpenAI-compatible local or self-hosted embedding servers."
        ),
    )
    semantic_embedding_api_key: str | None = Field(
        default=None,
        description=(
            "Optional API key passed directly to the LiteLLM embedding provider. "
            "When unset, LiteLLM continues to resolve credentials from provider "
            "environment variables such as OPENAI_API_KEY."
        ),
    )
    semantic_embedding_dimensions: int | None = Field(
        default=None,
        description=(
            "Embedding vector dimensions. Uses provider defaults when unset "
            "(384 for FastEmbed, 1536 for OpenAI and LiteLLM OpenAI default); "
            "required for custom LiteLLM models."
        ),
    )
    semantic_embedding_forward_dimensions: bool | None = Field(
        default=None,
        description=(
            "LiteLLM-only override for sending semantic_embedding_dimensions as a "
            "provider-side output-size request parameter. When unset, Basic Memory "
            "auto-detects model strings such as text-embedding-3."
        ),
    )
    # Trigger: full local rebuilds spend most of their time waiting behind shared
    # embed flushes, not constructing vectors themselves.
    # Why: smaller FastEmbed batches cut queue wait far more than they increase
    # write overhead on real-world projects, which makes full reindex materially faster.
    # Outcome: default to the smaller local/cloud-safe batch size we benchmarked as
    # the current best end-to-end setting in the shared vector sync pipeline.
    semantic_embedding_batch_size: int = Field(
        default=2,
        description="Batch size for embedding generation.",
        gt=0,
    )
    semantic_embedding_request_concurrency: int = Field(
        default=4,
        description="Maximum number of concurrent provider requests for batched embedding generation when the active provider supports request-level concurrency.",
        gt=0,
    )
    semantic_embedding_document_input_type: str | None = Field(
        default=None,
        description=(
            "Optional LiteLLM input_type for indexed document/passages. "
            "Use with asymmetric embedding models such as Cohere or NVIDIA retrieval models."
        ),
    )
    semantic_embedding_query_input_type: str | None = Field(
        default=None,
        description=(
            "Optional LiteLLM input_type for search queries. "
            "Use with asymmetric embedding models such as Cohere or NVIDIA retrieval models."
        ),
    )
    semantic_embedding_document_prefix: str | None = Field(
        default=None,
        description=(
            "Optional literal text prefix prepended to indexed document chunks before "
            "embedding. Use with prefix-sensitive asymmetric embedding models."
        ),
    )
    semantic_embedding_query_prefix: str | None = Field(
        default=None,
        description=(
            "Optional literal text prefix prepended to search queries before embedding. "
            "Use with prefix-sensitive asymmetric embedding models."
        ),
    )
    semantic_embedding_sync_batch_size: int = Field(
        default=2,
        description="Batch size for vector sync orchestration flushes.",
        gt=0,
    )
    semantic_postgres_prepare_concurrency: int = Field(
        default=4,
        description="Number of Postgres entity prepare tasks to run concurrently during vector sync. Postgres only; keep this low to avoid overdriving the database connection pool.",
        gt=0,
        le=16,
    )
    semantic_embedding_cache_dir: str | None = Field(
        default=None,
        description=(
            "Optional override for the FastEmbed model cache directory. "
            "When unset, Basic Memory resolves this at runtime to "
            "<basic-memory data dir>/fastembed_cache (or FASTEMBED_CACHE_PATH "
            "when that env var is set) so the model persists across runs "
            "without hardcoding a path into config.json."
        ),
    )
    semantic_embedding_threads: int | None = Field(
        default=None,
        description="Optional FastEmbed runtime thread count override.",
        gt=0,
    )
    semantic_embedding_parallel: int | None = Field(
        default=None,
        description="Optional FastEmbed embed() parallelism override.",
        gt=0,
    )
    import_upload_max_bytes: int = Field(
        default=100 * 1024 * 1024,
        description="Maximum uploaded JSON export size accepted by API import endpoints.",
        gt=0,
    )
    semantic_vector_k: int = Field(
        default=100,
        description="Vector candidate count for vector and hybrid retrieval.",
        gt=0,
    )
    semantic_min_similarity: float = Field(
        default=0.55,
        description="Minimum similarity score for vector search results. Results below this threshold are filtered out. 0.0 disables filtering.",
        ge=0.0,
        le=1.0,
    )
    default_search_type: Literal["text", "vector", "hybrid"] | None = Field(
        default=None,
        description="Default search type for search_notes when not specified per-query. "
        "Valid values: text, vector, hybrid. "
        "When unset, defaults to 'hybrid' if semantic search is enabled, otherwise 'text'.",
    )

    # Database connection pool configuration (Postgres only)
    db_pool_size: int = Field(
        default=20,
        description="Number of connections to keep in the pool (Postgres only)",
        gt=0,
    )
    db_pool_overflow: int = Field(
        default=40,
        description="Max additional connections beyond pool_size under load (Postgres only)",
        gt=0,
    )
    db_pool_recycle: int = Field(
        default=180,
        description="Recycle connections after N seconds to prevent stale connections. Default 180s works well with Neon's ~5 minute scale-to-zero (Postgres only)",
        gt=0,
    )

    # Background materialization (local runtime)
    materialization_workers: int = Field(
        default=4,
        description="Number of in-process workers that materialize accepted note "
        "writes (write the markdown file + index it) off the accept path. Bounds "
        "concurrent materializations so they don't contend en masse for the DB "
        "writer under high write load (local runtime; cloud uses queue workers).",
        gt=0,
    )

    # SQLite tuning. The index DB is a cache rebuildable from the markdown files
    # (the source of truth), so durability *could* be traded for throughput — but
    # benchmarks showed OFF buys nothing over the NORMAL default (WAL + NORMAL
    # already skips the per-commit fsync), so the safe default stays.
    sqlite_synchronous: Literal["OFF", "NORMAL", "FULL", "EXTRA"] = Field(
        default="NORMAL",
        description="SQLite `PRAGMA synchronous`. Default NORMAL — safe with WAL "
        "(survives app crashes; only an OS crash/power loss can lose the last "
        "transactions). OFF showed no measurable write-throughput gain in "
        "benchmarks (WAL+NORMAL already avoids the per-commit fsync), so it is only "
        "worth setting for callers that knowingly trade durability — the index DB "
        "rebuilds from the markdown files via sync — e.g. a one-off bulk import.",
    )
    sqlite_mmap_size: int = Field(
        default=268435456,  # 256 MB
        description="SQLite `PRAGMA mmap_size` in bytes (0 disables memory-mapped "
        "I/O). Speeds reads — including the lookups inside writes (link/permalink "
        "resolution, FTS).",
        ge=0,
    )
    sqlite_wal_autocheckpoint: int = Field(
        default=1000,
        description="SQLite `PRAGMA wal_autocheckpoint` in pages (0 disables "
        "automatic checkpoints). Higher values checkpoint less often, reducing "
        "writer stalls under sustained bursts at the cost of a larger WAL.",
        ge=0,
    )
    sqlite_page_size: int = Field(
        default=4096,
        description="SQLite `PRAGMA page_size` in bytes (power of two, 512-65536). "
        "Only takes effect on a freshly created database (or after VACUUM).",
        ge=512,
        le=65536,
    )

    # Watch service configuration
    index_delay: int = Field(
        default=1000, description="Milliseconds to wait after changes before indexing", gt=0
    )

    watch_project_reload_interval: int = Field(
        default=300,
        description="Seconds between reloading project list in watch service. Higher values reduce CPU usage by minimizing watcher restarts. Default 300s (5 min) balances efficiency with responsiveness to new projects.",
        gt=0,
    )

    # update permalinks on move
    update_permalinks_on_move: bool = Field(
        default=False,
        description="Whether to update permalinks when files are moved or renamed. default (False)",
    )

    index_changes: bool = Field(
        default=True,
        description="Whether to index local file changes in real time. default (True)",
    )
    index_batch_size: int = Field(
        default=32,
        description="Maximum number of changed files to load into one indexing batch.",
        gt=0,
    )
    index_batch_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        description="Maximum total bytes to load into one indexing batch. Large files still run as single-file batches.",
        gt=0,
    )
    index_parse_max_concurrent: int = Field(
        default=8,
        description="Maximum number of markdown parse tasks to run concurrently inside one indexing batch.",
        gt=0,
    )
    index_entity_max_concurrent: int = Field(
        default=4,
        description="Maximum number of entity create/update tasks to run concurrently inside one indexing batch.",
        gt=0,
    )
    index_metadata_update_max_concurrent: int = Field(
        default=4,
        description="Maximum number of metadata/search refresh tasks to run concurrently inside one indexing batch.",
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

    write_note_overwrite_default: bool = Field(
        default=False,
        description=(
            "Default value for write_note's overwrite parameter. "
            "When False (default), write_note errors if note already exists. "
            "Set to True to restore pre-v0.20 upsert behavior. "
            "Env: BASIC_MEMORY_WRITE_NOTE_OVERWRITE_DEFAULT"
        ),
    )

    cli_output_style: Literal["rich", "plain"] = Field(
        default="rich",
        description=(
            "Default human-readable output style for interactive `bm tool` commands "
            "(search-notes, read-note, build-context, recent-activity) when stdout is a TTY. "
            "'rich' (default) renders colored Panel/Table/Tree/Markdown output; "
            "'plain' renders undecorated greppable text with no ANSI colors or box-drawing. "
            "Overridden per-invocation by --json (raw JSON) or --plain (forces plain). "
            "Env: BASIC_MEMORY_CLI_OUTPUT_STYLE"
        ),
    )

    ensure_frontmatter_on_sync: bool = Field(
        default=True,
        description="Ensure markdown files have frontmatter during sync by adding derived title/type/permalink when missing. When combined with disable_permalinks=True, this setting takes precedence for missing-frontmatter files and still writes permalinks.",
    )

    permalinks_include_project: bool = Field(
        default=True,
        description="When True, generated permalinks are prefixed with the project slug (e.g., 'specs/search'). Existing permalinks remain unchanged unless explicitly updated.",
    )

    skip_initialization_sync: bool = Field(
        default=False,
        description="Skip expensive initialization synchronization. Useful for cloud/stateless deployments where project reconciliation is not needed.",
    )

    # File formatting configuration
    format_on_save: bool = Field(
        default=False,
        description="Automatically format files after saving using configured formatter. Disabled by default.",
    )

    formatter_command: Optional[str] = Field(
        default=None,
        description="External formatter command. Use {file} as placeholder for file path. If not set, uses built-in mdformat (Python, no Node.js required). Set to 'npx prettier --write {file}' for Prettier.",
    )

    formatters: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-extension formatters. Keys are extensions (without dot), values are commands. Example: {'md': 'prettier --write {file}', 'json': 'prettier --write {file}'}",
    )

    formatter_timeout: float = Field(
        default=5.0,
        description="Maximum seconds to wait for formatter to complete",
        gt=0,
    )

    # Project path constraints
    project_root: Optional[str] = Field(
        default=None,
        description="If set, all projects must be created underneath this directory. Paths will be sanitized and constrained to this root. If not set, projects can be created anywhere (default behavior).",
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

    cloud_promo_opt_out: bool = Field(
        default=False,
        description="Disable CLI cloud promo messages when true.",
    )

    cloud_promo_first_run_shown: bool = Field(
        default=False,
        description="Tracks whether the first-run cloud promo message has been shown.",
    )

    cloud_promo_last_version_shown: Optional[str] = Field(
        default=None,
        description="Most recent cloud promo version shown in CLI.",
    )

    auto_update: bool = Field(
        default=True,
        description="Enable automatic CLI update checks and installs when supported.",
    )

    update_check_interval: int = Field(
        default=86400,
        description="Seconds between automatic update checks.",
        gt=0,
    )

    auto_update_last_checked_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the last attempted automatic update check.",
    )

    cloud_api_key: Optional[str] = Field(
        default=None,
        description="API key for cloud access (bmc_ prefixed). Account-level, not per-project.",
    )

    default_workspace: Optional[str] = Field(
        default=None,
        description="Default cloud workspace tenant_id. Set by 'bm cloud workspace set-default'.",
    )

    # Legacy config keys / env vars mapped to their renamed fields.
    _LEGACY_SYNC_FIELDS: ClassVar[dict[str, str]] = {
        "index_changes": "sync_changes",
        "index_delay": "sync_delay",
    }

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_sync_fields(cls, data: Any) -> Any:
        """Honor legacy ``sync_changes``/``sync_delay`` after their rename to
        ``index_changes``/``index_delay``.

        Existing configs that set ``"sync_changes": false`` (disable realtime
        indexing) or a custom ``"sync_delay"`` debounce must keep that behavior
        across the rename. ``extra="ignore"`` would otherwise drop the unknown
        keys and fall back to the new defaults (True / 1000ms), silently
        restarting the watcher or speeding up indexing for users who tuned it.
        The new field takes precedence when both are present.

        Both surfaces must migrate: config.json ships the legacy *dict keys*,
        while env-var users set ``BASIC_MEMORY_SYNC_CHANGES`` /
        ``BASIC_MEMORY_SYNC_DELAY``. pydantic-settings ignores those env vars
        (they aren't model fields), and ConfigManager's env-merge loop only
        probes ``BASIC_MEMORY_{new field}`` — so without this the env-var
        rename is silently dropped. Env overrides the legacy file key to match
        normal env > file precedence; the new field name always wins.
        """
        return migrate_legacy_sync_fields(
            data,
            legacy_fields=cls._LEGACY_SYNC_FIELDS,
            env_prefix=str(cls.model_config["env_prefix"]),
        )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_projects(cls, data: Any) -> Any:
        """Migrate old-format config (Dict[str, str]) to new ProjectEntry format.

        Old format stored projects as three separate dicts:
          projects:      {"name": "/path"}
          project_modes: {"name": "cloud"}
          cloud_projects: {"name": {"local_path": "...", ...}}

        New format unifies them into:
          projects: {"name": {"path": "/path", "mode": "cloud", ...}}

        Also removes stale keys (default_project_mode, permalinks_include_project)
        that are no longer part of the config model.
        """
        return migrate_legacy_projects(data)

    @property
    def is_test_env(self) -> bool:
        """Check if running in a test environment.

        Returns True if any of:
        - env field is set to "test"
        - BASIC_MEMORY_ENV environment variable is "test"
        - PYTEST_CURRENT_TEST environment variable is set (pytest is running)

        Used to disable features like file watchers during tests.
        """
        return (
            self.env == "test"
            or os.getenv("BASIC_MEMORY_ENV", "").lower() == "test"
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )

    @property
    def cloud_mode(self) -> bool:
        """Whether this process runs as a cloud deployment.

        In-repo cloud containers build BasicMemoryConfig via ConfigManager (not
        for_cloud_tenant), so they signal cloud mode through the environment
        rather than skip_initialization_sync. Mirrors the detection in setup_logging.
        """
        return os.getenv("BASIC_MEMORY_CLOUD_MODE", "").lower() in ("1", "true")

    @property
    def skip_local_initialization(self) -> bool:
        """Whether to skip local project seeding / reconciliation / path creation.

        True for any cloud or stateless deployment: for_cloud_tenant sets
        skip_initialization_sync, while in-repo cloud containers set
        BASIC_MEMORY_CLOUD_MODE. A LOCAL Postgres install matches neither, so it
        still initializes like SQLite. Gating these paths on the Postgres *backend*
        caught local Postgres (wrong); gating only on skip_initialization_sync
        missed BASIC_MEMORY_CLOUD_MODE deployments, letting reconcile delete tenant
        project rows (also wrong).
        """
        return self.skip_initialization_sync or self.cloud_mode

    def get_project_mode(self, project_name: str) -> ProjectMode:
        """Get the routing mode for a project.

        Returns the per-project mode if set.
        Unknown projects (not in local config) default to CLOUD —
        local projects are always registered in config.
        """
        entry = self.projects.get(project_name)
        return entry.mode if entry else ProjectMode.CLOUD

    def is_locally_syncable(self, project_name: str, project_path: str) -> bool:
        """Whether a project should be synced/watched on the local filesystem.

        Both conditions are required (issue #949):

          * The project is present in config. Config is the source of truth, so a
            stale database row that was removed from config — but whose deletion
            has not yet been reconciled, or whose reconciliation failed — must
            not be synced even though it still has a real directory on disk.
          * Its path is absolute. An empty or relative path resolves against the
            process cwd, so syncing it would adopt whatever directory the server
            was launched from as the project root and mutate unrelated files.

        Cloud-only projects (empty/slug path) and cloud projects with a real
        local bisync copy (absolute path) are handled correctly by these two
        conditions, so no separate mode check is needed.
        """
        entry = self.projects.get(project_name)
        return entry is not None and Path(project_path).is_absolute()

    def set_project_mode(self, project_name: str, mode: ProjectMode) -> None:
        """Set the routing mode for a project.

        Creates a minimal ProjectEntry if the project doesn't already exist,
        preserving backward compatibility with code that sets mode before
        adding a full project entry.
        """
        if project_name in self.projects:
            self.projects[project_name].mode = mode
        else:
            self.projects[project_name] = ProjectEntry(path="", mode=mode)

    @classmethod
    def for_cloud_tenant(
        cls,
        database_url: str,
        projects: Optional[Dict[str, "ProjectEntry"]] = None,
    ) -> "BasicMemoryConfig":
        """Create config for cloud tenant - no config.json, database is source of truth.

        This factory method creates a BasicMemoryConfig suitable for cloud deployments
        where:
        - Database is Postgres (Neon), not SQLite
        - Projects are discovered from the database, not config file
        - Path validation is skipped (no local filesystem in cloud)
        - Initialization sync is skipped (stateless deployment)

        Args:
            database_url: Postgres connection URL for tenant database
            projects: Optional project mapping (usually empty, discovered from DB)

        Returns:
            BasicMemoryConfig configured for cloud mode
        """
        return cls(  # pragma: no cover
            database_backend=DatabaseBackend.POSTGRES,
            database_url=database_url,
            projects=projects or {},
            skip_initialization_sync=True,
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

        return Path(self.projects[name].path)

    def model_post_init(self, __context: Any) -> None:
        """Ensure configuration is valid after initialization."""
        # Skip default-project seeding only for cloud/stateless deployments, where
        # projects are discovered from the database per tenant. See
        # skip_local_initialization for why this is not gated on the Postgres
        # backend (caught local Postgres) nor on skip_initialization_sync alone
        # (missed BASIC_MEMORY_CLOUD_MODE deployments).
        if self.skip_local_initialization:
            return

        # Trigger: no projects configured (fresh install or empty config)
        # Why: every config needs at least one project to be functional
        # Outcome: creates "main" project using BASIC_MEMORY_HOME or ~/basic-memory
        if not self.projects:
            self.projects["main"] = ProjectEntry(
                path=str(Path(os.getenv("BASIC_MEMORY_HOME", Path.home() / "basic-memory")))
            )

        # Trigger: default_project was not explicitly provided in the input data
        #          (config file omitted the key, or BasicMemoryConfig() called with no args)
        # Why: callers like get_project_config() expect a valid project name;
        #      but explicit None (discovery mode) must be preserved
        # Outcome: sets default_project to the first available project
        if "default_project" not in self.model_fields_set:
            self.default_project = next(iter(self.projects.keys()))
        # Trigger: default_project was explicitly set but references a non-existent project
        # Why: project may have been removed or renamed since config was saved
        # Outcome: corrects to the first available project
        elif self.default_project is not None and self.default_project not in self.projects:
            self.default_project = next(iter(self.projects.keys()))

    @property
    def app_database_path(self) -> Path:
        """Get the path to the app-level database.

        This is the single database that will store all knowledge data
        across all projects.

        Uses BASIC_MEMORY_CONFIG_DIR when set so each process/worktree can
        isolate both config and database state.
        """
        database_path = self.data_dir_path / APP_DATABASE_NAME
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
        from basic_memory.config import ConfigManager

        config_manager = ConfigManager()
        config = config_manager.load_config()  # pragma: no cover
        return config.app_database_path  # pragma: no cover

    @property
    def project_list(self) -> List[ProjectConfig]:  # pragma: no cover
        """Get all configured projects as ProjectConfig objects."""
        return [
            ProjectConfig(name=name, home=Path(entry.path), mode=entry.mode)
            for name, entry in self.projects.items()
        ]

    @model_validator(mode="after")
    def ensure_project_paths_exists(self) -> "BasicMemoryConfig":  # pragma: no cover
        """Ensure project paths exist.

        Skips path creation for cloud/stateless deployments, whose tenants don't
        use local filesystem paths. A local Postgres install still needs its
        project directories created like SQLite, so gate on
        skip_local_initialization, not the backend — otherwise the seeded default's
        directory is never created and the sync/watch path hits a non-existent
        directory.
        """
        if self.skip_local_initialization:
            return self

        for name, entry in self.projects.items():
            path = Path(entry.path)
            # Skip cloud-only projects whose path is a slug, not a local directory
            if not path.is_absolute():
                continue
            if not path.exists():
                try:
                    path.mkdir(parents=True)
                except Exception as e:
                    logger.error(f"Failed to create project path: {e}")
                    raise e
        return self

    @property
    def data_dir_path(self) -> Path:
        """Get app state directory for config and default SQLite database."""
        return resolve_data_dir()
