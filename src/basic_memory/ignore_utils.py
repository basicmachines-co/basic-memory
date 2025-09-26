"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
from pathlib import Path
from typing import Set


# Common directories and patterns to ignore by default
DEFAULT_IGNORE_PATTERNS = {
    ".git",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".coverage",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "Thumbs.db",
    ".idea",
    ".vscode",
    "*.egg-info",
    "build",
    "dist",
    ".tox",
    ".cache",
    ".mypy_cache",
    ".ruff_cache",
    ".obsidian",
    ".idea",
}


def load_gitignore_patterns(base_path: Path) -> Set[str]:
    """Load gitignore patterns from .gitignore file and add default patterns.

    Args:
        base_path: The base directory to search for .gitignore file

    Returns:
        Set of patterns to ignore
    """
    patterns = set(DEFAULT_IGNORE_PATTERNS)

    gitignore_file = base_path / ".gitignore"
    if gitignore_file.exists():
        try:
            with gitignore_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith("#"):
                        patterns.add(line)
        except Exception:
            # If we can't read .gitignore, just use default patterns
            pass

    return patterns


def should_ignore_path(file_path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """Check if a file path should be ignored based on gitignore patterns.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        True if the path should be ignored, False otherwise
    """
    # Get the relative path from base
    try:
        relative_path = file_path.relative_to(base_path)
        relative_str = str(relative_path)
        relative_posix = relative_path.as_posix()  # Use forward slashes for matching

        # Check each pattern
        for pattern in ignore_patterns:
            # Handle patterns starting with / (root relative)
            if pattern.startswith("/"):
                root_pattern = pattern[1:]  # Remove leading /

                # For directory patterns ending with /
                if root_pattern.endswith("/"):
                    dir_name = root_pattern[:-1]  # Remove trailing /
                    # Check if the first part of the path matches the directory name
                    if len(relative_path.parts) > 0 and relative_path.parts[0] == dir_name:
                        return True
                else:
                    # Regular root-relative pattern
                    if fnmatch.fnmatch(relative_posix, root_pattern):
                        return True
                continue

            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_name = pattern[:-1]  # Remove trailing /
                # Check if any path part matches the directory name
                if dir_name in relative_path.parts:
                    return True
                continue

            # Direct name match (e.g., ".git", "node_modules")
            if pattern in relative_path.parts:
                return True

            # Glob pattern match
            if fnmatch.fnmatch(relative_posix, pattern) or fnmatch.fnmatch(relative_str, pattern):
                return True

        return False
    except ValueError:
        # If we can't get relative path, don't ignore
        return False


def filter_files(
    files: list[Path], base_path: Path, ignore_patterns: Set[str] | None = None
) -> tuple[list[Path], int]:
    """Filter a list of files based on gitignore patterns.

    Args:
        files: List of file paths to filter
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to ignore. If None, loads from .gitignore

    Returns:
        Tuple of (filtered_files, ignored_count)
    """
    if ignore_patterns is None:
        ignore_patterns = load_gitignore_patterns(base_path)

    filtered_files = []
    ignored_count = 0

    for file_path in files:
        if should_ignore_path(file_path, base_path, ignore_patterns):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
