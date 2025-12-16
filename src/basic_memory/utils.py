"""Utility functions for basic-memory."""

import os

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Union, runtime_checkable, List

from loguru import logger
from unidecode import unidecode


def normalize_project_path(path: str) -> str:
    """Normalize project path by stripping mount point prefix.

    In cloud deployments, the S3 bucket is mounted at /app/data. We strip this
    prefix from project paths to avoid leaking implementation details and to
    ensure paths match the actual S3 bucket structure.

    For local paths (including Windows paths), returns the path unchanged.

    Args:
        path: Project path (e.g., "/app/data/basic-memory-llc" or "C:\\Users\\...")

    Returns:
        Normalized path (e.g., "/basic-memory-llc" or "C:\\Users\\...")

    Examples:
        >>> normalize_project_path("/app/data/my-project")
        '/my-project'
        >>> normalize_project_path("/my-project")
        '/my-project'
        >>> normalize_project_path("app/data/my-project")
        '/my-project'
        >>> normalize_project_path("C:\\\\Users\\\\project")
        'C:\\\\Users\\\\project'
    """
    # Check if this is a Windows absolute path (e.g., C:\Users\...)
    # Windows paths have a drive letter followed by a colon
    if len(path) >= 2 and path[1] == ":":
        # Windows absolute path - return unchanged
        return path

    # Handle both absolute and relative Unix paths
    normalized = path.lstrip("/")
    if normalized.startswith("app/data/"):
        normalized = normalized.removeprefix("app/data/")

    # Ensure leading slash for Unix absolute paths
    if not normalized.startswith("/"):
        normalized = "/" + normalized

    return normalized


@runtime_checkable
class PathLike(Protocol):
    """Protocol for objects that can be used as paths."""

    def __str__(self) -> str: ...


# In type annotations, use Union[Path, str] instead of FilePath for now
# This preserves compatibility with existing code while we migrate
FilePath = Union[Path, str]


def generate_permalink(file_path: Union[Path, str, PathLike], split_extension: bool = True) -> str:
    """Generate a stable permalink from a file path.

    Args:
        file_path: Original file path (str, Path, or PathLike)
        split_extension: Whether to split off and discard file extensions.
                        When True, uses mimetypes to detect real extensions.
                        When False, preserves all content including periods.

    Returns:
        Normalized permalink that matches validation rules. Converts spaces and underscores
        to hyphens for consistency. Preserves non-ASCII characters like Chinese.
        Preserves periods in version numbers (e.g., "2.0.0") when they're not real file extensions.

    Examples:
        >>> generate_permalink("docs/My Feature.md")
        'docs/my-feature'
        >>> generate_permalink("specs/API (v2).md")
        'specs/api-v2'
        >>> generate_permalink("design/unified_model_refactor.md")
        'design/unified-model-refactor'
        >>> generate_permalink("中文/测试文档.md")
        '中文/测试文档'
        >>> generate_permalink("Version 2.0.0")
        'version-2.0.0'
    """
    # Convert Path to string if needed
    path_str = Path(str(file_path)).as_posix()

    # Only split extension if there's a real file extension
    # Use mimetypes to detect real extensions, avoiding misinterpreting periods in version numbers
    import mimetypes

    mime_type, _ = mimetypes.guess_type(path_str)
    has_real_extension = mime_type is not None

    if has_real_extension and split_extension:
        # Real file extension detected - split it off
        (base, extension) = os.path.splitext(path_str)
    else:
        # No real extension or split_extension=False - process the whole string
        base = path_str
        extension = ""

    # Check if we have CJK characters that should be preserved
    # CJK ranges: \u4e00-\u9fff (CJK Unified Ideographs), \u3000-\u303f (CJK symbols),
    # \u3400-\u4dbf (CJK Extension A), \uff00-\uffef (Fullwidth forms)
    has_cjk_chars = any(
        "\u4e00" <= char <= "\u9fff"
        or "\u3000" <= char <= "\u303f"
        or "\u3400" <= char <= "\u4dbf"
        or "\uff00" <= char <= "\uffef"
        for char in base
    )

    if has_cjk_chars:
        # For text with CJK characters, selectively transliterate only Latin accented chars
        result = ""
        for char in base:
            if (
                "\u4e00" <= char <= "\u9fff"
                or "\u3000" <= char <= "\u303f"
                or "\u3400" <= char <= "\u4dbf"
            ):
                # Preserve CJK ideographs and symbols
                result += char
            elif "\uff00" <= char <= "\uffef":
                # Remove Chinese fullwidth punctuation entirely (like ，！？)
                continue
            else:
                # Transliterate Latin accented characters to ASCII
                result += unidecode(char)

        # Insert hyphens between CJK and Latin character transitions
        # Match: CJK followed by Latin letter/digit, or Latin letter/digit followed by CJK
        result = re.sub(
            r"([\u4e00-\u9fff\u3000-\u303f\u3400-\u4dbf])([a-zA-Z0-9])", r"\1-\2", result
        )
        result = re.sub(
            r"([a-zA-Z0-9])([\u4e00-\u9fff\u3000-\u303f\u3400-\u4dbf])", r"\1-\2", result
        )

        # Insert dash between camelCase
        result = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", result)

        # Convert ASCII letters to lowercase, preserve CJK
        lower_text = "".join(c.lower() if c.isascii() and c.isalpha() else c for c in result)

        # Replace underscores with hyphens
        text_with_hyphens = lower_text.replace("_", "-")

        # Remove apostrophes entirely (don't replace with hyphens)
        text_no_apostrophes = text_with_hyphens.replace("'", "")

        # Replace unsafe chars with hyphens, but preserve CJK characters and periods
        clean_text = re.sub(
            r"[^a-z0-9\u4e00-\u9fff\u3000-\u303f\u3400-\u4dbf/\-\.]", "-", text_no_apostrophes
        )
    else:
        # Original ASCII-only processing for backward compatibility
        # Transliterate unicode to ascii
        ascii_text = unidecode(base)

        # Insert dash between camelCase
        ascii_text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", ascii_text)

        # Convert to lowercase
        lower_text = ascii_text.lower()

        # replace underscores with hyphens
        text_with_hyphens = lower_text.replace("_", "-")

        # Remove apostrophes entirely (don't replace with hyphens)
        text_no_apostrophes = text_with_hyphens.replace("'", "")

        # Replace remaining invalid chars with hyphens, preserving periods
        clean_text = re.sub(r"[^a-z0-9/\-\.]", "-", text_no_apostrophes)

    # Collapse multiple hyphens
    clean_text = re.sub(r"-+", "-", clean_text)

    # Clean each path segment
    segments = clean_text.split("/")
    clean_segments = [s.strip("-") for s in segments]

    return_val = "/".join(clean_segments)

    # Append file extension back, if necessary
    if not split_extension and extension:
        return_val += extension

    return return_val


def setup_logging(
    log_level: str = "INFO",
    log_to_file: bool = False,
    log_to_stdout: bool = False,
    structured_context: bool = False,
) -> None:  # pragma: no cover
    """Configure logging with explicit settings.

    This function provides a simple, explicit interface for configuring logging.
    Each entry point (CLI, MCP, API) should call this with appropriate settings.

    Args:
        log_level: DEBUG, INFO, WARNING, ERROR
        log_to_file: Write to ~/.basic-memory/basic-memory.log with rotation
        log_to_stdout: Write to stderr (for Docker/cloud deployments)
        structured_context: Bind tenant_id, fly_region, etc. for cloud observability
    """
    # Remove default handler and any existing handlers
    logger.remove()

    # In test mode, only log to stdout regardless of settings
    env = os.getenv("BASIC_MEMORY_ENV", "dev")
    if env == "test":
        logger.add(sys.stderr, level=log_level, backtrace=True, diagnose=True, colorize=True)
        return

    # Add file handler with rotation
    if log_to_file:
        log_path = Path.home() / ".basic-memory" / "basic-memory.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level=log_level,
            rotation="10 MB",
            retention="10 days",
            backtrace=True,
            diagnose=True,
            enqueue=True,  # Thread-safe async logging
            colorize=False,
        )

    # Add stdout handler (for Docker/cloud)
    if log_to_stdout:
        logger.add(sys.stderr, level=log_level, backtrace=True, diagnose=True, colorize=True)

    # Bind structured context for cloud observability
    if structured_context:
        logger.configure(
            extra={
                "tenant_id": os.getenv("BASIC_MEMORY_TENANT_ID", "local"),
                "fly_app_name": os.getenv("FLY_APP_NAME", "local"),
                "fly_machine_id": os.getenv("FLY_MACHINE_ID", "local"),
                "fly_region": os.getenv("FLY_REGION", "local"),
            }
        )

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)


def parse_tags(tags: Union[List[str], str, None]) -> List[str]:
    """Parse tags from various input formats into a consistent list.

    Args:
        tags: Can be a list of strings, a comma-separated string, or None

    Returns:
        A list of tag strings, or an empty list if no tags

    Note:
        This function strips leading '#' characters from tags to prevent
        their accumulation when tags are processed multiple times.
    """
    if tags is None:
        return []

    # Process list of tags
    if isinstance(tags, list):
        # First strip whitespace, then strip leading '#' characters to prevent accumulation
        return [tag.strip().lstrip("#") for tag in tags if tag and tag.strip()]

    # Process string input
    if isinstance(tags, str):
        # Check if it's a JSON array string (common issue from AI assistants)
        import json

        if tags.strip().startswith("[") and tags.strip().endswith("]"):
            try:
                # Try to parse as JSON array
                parsed_json = json.loads(tags)
                if isinstance(parsed_json, list):
                    # Recursively parse the JSON array as a list
                    return parse_tags(parsed_json)
            except json.JSONDecodeError:
                # Not valid JSON, fall through to comma-separated parsing
                pass

        # Split by comma, strip whitespace, then strip leading '#' characters
        return [tag.strip().lstrip("#") for tag in tags.split(",") if tag and tag.strip()]

    # For any other type, try to convert to string and parse
    try:  # pragma: no cover
        return parse_tags(str(tags))
    except (ValueError, TypeError):  # pragma: no cover
        logger.warning(f"Couldn't parse tags from input of type {type(tags)}: {tags}")
        return []


def normalize_newlines(multiline: str) -> str:
    """Replace any \r\n, \r, or \n with the native newline.

    Args:
        multiline: String containing any mixture of newlines.

    Returns:
        A string with normalized newlines native to the platform.
    """
    return re.sub(r"\r\n?|\n", os.linesep, multiline)


def normalize_file_path_for_comparison(file_path: str) -> str:
    """Normalize a file path for conflict detection.

    This function normalizes file paths to help detect potential conflicts:
    - Converts to lowercase for case-insensitive comparison
    - Normalizes Unicode characters
    - Converts backslashes to forward slashes for cross-platform consistency

    Args:
        file_path: The file path to normalize

    Returns:
        Normalized file path for comparison purposes
    """
    import unicodedata
    from pathlib import PureWindowsPath

    # Use PureWindowsPath to ensure backslashes are treated as separators
    # regardless of current platform, then convert to POSIX-style
    normalized = PureWindowsPath(file_path).as_posix().lower()

    # Normalize Unicode characters (NFD normalization)
    normalized = unicodedata.normalize("NFD", normalized)

    return normalized


def detect_potential_file_conflicts(file_path: str, existing_paths: List[str]) -> List[str]:
    """Detect potential conflicts between a file path and existing paths.

    This function checks for various types of conflicts:
    - Case sensitivity differences
    - Unicode normalization differences
    - Path separator differences
    - Permalink generation conflicts

    Args:
        file_path: The file path to check
        existing_paths: List of existing file paths to check against

    Returns:
        List of existing paths that might conflict with the given file path
    """
    conflicts = []

    # Normalize the input file path
    normalized_input = normalize_file_path_for_comparison(file_path)
    input_permalink = generate_permalink(file_path)

    for existing_path in existing_paths:
        # Skip identical paths
        if existing_path == file_path:
            continue

        # Check for case-insensitive path conflicts
        normalized_existing = normalize_file_path_for_comparison(existing_path)
        if normalized_input == normalized_existing:
            conflicts.append(existing_path)
            continue

        # Check for permalink conflicts
        existing_permalink = generate_permalink(existing_path)
        if input_permalink == existing_permalink:
            conflicts.append(existing_path)
            continue

    return conflicts


def valid_project_path_value(path: str):
    """Ensure project path is valid."""
    # Allow empty strings as they resolve to the project root
    if not path:
        return True

    # Check for obvious path traversal patterns first
    if ".." in path or "~" in path:
        return False

    # Check for Windows-style path traversal (even on Unix systems)
    if "\\.." in path or path.startswith("\\"):
        return False

    # Block absolute paths (Unix-style starting with / or Windows-style with drive letters)
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        return False

    # Block paths with control characters (but allow whitespace that will be stripped)
    if path.strip() and any(ord(c) < 32 and c not in [" ", "\t"] for c in path):
        return False

    return True


def validate_project_path(path: str, project_path: Path) -> bool:
    """Ensure path is valid and stays within project boundaries."""

    if not valid_project_path_value(path):
        return False

    try:
        resolved = (project_path / path).resolve()
        return resolved.is_relative_to(project_path.resolve())
    except (ValueError, OSError):
        return False


def ensure_timezone_aware(dt: datetime, cloud_mode: bool | None = None) -> datetime:
    """Ensure a datetime is timezone-aware.

    If the datetime is naive, convert it to timezone-aware. The interpretation
    depends on cloud_mode:
    - In cloud mode (PostgreSQL/asyncpg): naive datetimes are interpreted as UTC
    - In local mode (SQLite): naive datetimes are interpreted as local time

    asyncpg uses binary protocol which returns timestamps in UTC but as naive
    datetimes. In cloud deployments, cloud_mode=True handles this correctly.

    Args:
        dt: The datetime to ensure is timezone-aware
        cloud_mode: Optional explicit cloud_mode setting. If None, loads from config.

    Returns:
        A timezone-aware datetime
    """
    if dt.tzinfo is None:
        # Determine cloud_mode: use explicit parameter if provided, otherwise load from config
        if cloud_mode is None:
            from basic_memory.config import ConfigManager

            cloud_mode = ConfigManager().config.cloud_mode_enabled

        if cloud_mode:
            # Cloud/PostgreSQL mode: naive datetimes from asyncpg are already UTC
            return dt.replace(tzinfo=timezone.utc)
        else:
            # Local/SQLite mode: naive datetimes are in local time
            return dt.astimezone()
    else:
        # Already timezone-aware
        return dt
