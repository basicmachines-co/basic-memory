"""Utilities for file operations."""

import hashlib
from pathlib import Path
from typing import Any, Dict, Union

import yaml
from loguru import logger

from basic_memory.utils import FilePath


class FileError(Exception):
    """Base exception for file operations."""

    pass


class FileWriteError(FileError):
    """Raised when file operations fail."""

    pass


class ParseError(FileError):
    """Raised when parsing file content fails."""

    pass


async def compute_checksum(content: Union[str, bytes]) -> str:
    """
    Compute SHA-256 checksum of content.

    Args:
        content: Content to hash (either text string or bytes)

    Returns:
        SHA-256 hex digest

    Raises:
        FileError: If checksum computation fails
    """
    try:
        if isinstance(content, str):
            content = content.encode()
        return hashlib.sha256(content).hexdigest()
    except Exception as e:  # pragma: no cover
        logger.error(f"Failed to compute checksum: {e}")
        raise FileError(f"Failed to compute checksum: {e}")


async def ensure_directory(path: FilePath) -> None:
    """
    Ensure directory exists, creating if necessary.

    Args:
        path: Directory path to ensure (Path or string)

    Raises:
        FileWriteError: If directory creation fails
    """
    try:
        # Convert string to Path if needed
        path_obj = Path(path) if isinstance(path, str) else path
        path_obj.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # pragma: no cover
        logger.error("Failed to create directory", path=str(path), error=str(e))
        raise FileWriteError(f"Failed to create directory {path}: {e}")


async def write_file_atomic(path: FilePath, content: str) -> None:
    """
    Write file with atomic operation using temporary file.

    Args:
        path: Target file path (Path or string)
        content: Content to write

    Raises:
        FileWriteError: If write operation fails
    """
    # Convert string to Path if needed
    path_obj = Path(path) if isinstance(path, str) else path
    temp_path = path_obj.with_suffix(".tmp")

    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path_obj)
        logger.debug("Wrote file atomically", path=str(path_obj), content_length=len(content))
    except Exception as e:  # pragma: no cover
        temp_path.unlink(missing_ok=True)
        logger.error("Failed to write file", path=str(path_obj), error=str(e))
        raise FileWriteError(f"Failed to write file {path}: {e}")


def has_frontmatter(content: str) -> bool:
    """
    Check if content contains valid YAML frontmatter.

    Args:
        content: Content to check

    Returns:
        True if content has valid frontmatter markers (---), False otherwise
    """
    if not content:
        return False

    content = content.strip()
    if not content.startswith("---"):
        return False

    return "---" in content[3:]


def parse_frontmatter(content: str) -> Dict[str, Any]:
    """
    Parse YAML frontmatter from content.

    Args:
        content: Content with YAML frontmatter

    Returns:
        Dictionary of frontmatter values

    Raises:
        ParseError: If frontmatter is invalid or parsing fails
    """
    try:
        if not content.strip().startswith("---"):
            raise ParseError("Content has no frontmatter")

        # Split on first two occurrences of ---
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ParseError("Invalid frontmatter format")

        # Parse YAML
        try:
            frontmatter = yaml.safe_load(parts[1])
            # Handle empty frontmatter (None from yaml.safe_load)
            if frontmatter is None:
                return {}
            if not isinstance(frontmatter, dict):
                raise ParseError("Frontmatter must be a YAML dictionary")
            return frontmatter

        except yaml.YAMLError as e:
            raise ParseError(f"Invalid YAML in frontmatter: {e}")

    except Exception as e:  # pragma: no cover
        if not isinstance(e, ParseError):
            logger.error(f"Failed to parse frontmatter: {e}")
            raise ParseError(f"Failed to parse frontmatter: {e}")
        raise


def remove_frontmatter(content: str) -> str:
    """
    Remove YAML frontmatter from content.

    Args:
        content: Content with frontmatter

    Returns:
        Content with frontmatter removed, or original content if no frontmatter

    Raises:
        ParseError: If content starts with frontmatter marker but is malformed
    """
    content = content.strip()

    # Return as-is if no frontmatter marker
    if not content.startswith("---"):
        return content

    # Split on first two occurrences of ---
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ParseError("Invalid frontmatter format")

    return parts[2].strip()


async def update_frontmatter(path: FilePath, updates: Dict[str, Any]) -> str:
    """Update frontmatter fields in a file while preserving all content.

    Only modifies the frontmatter section, leaving all content untouched.
    Creates frontmatter section if none exists.
    Returns checksum of updated file.

    Args:
        path: Path to markdown file (Path or string)
        updates: Dict of frontmatter fields to update

    Returns:
        Checksum of updated file

    Raises:
        FileError: If file operations fail
        ParseError: If frontmatter parsing fails
    """
    try:
        # Convert string to Path if needed
        path_obj = Path(path) if isinstance(path, str) else path

        # Read current content
        content = path_obj.read_text(encoding="utf-8")

        # Parse current frontmatter
        current_fm = {}
        if has_frontmatter(content):
            current_fm = parse_frontmatter(content)
            content = remove_frontmatter(content)

        # Update frontmatter
        new_fm = {**current_fm, **updates}

        # Write new file with updated frontmatter
        yaml_fm = yaml.dump(new_fm, sort_keys=False, allow_unicode=True)
        final_content = f"---\n{yaml_fm}---\n\n{content.strip()}"

        logger.debug("Updating frontmatter", path=str(path_obj), update_keys=list(updates.keys()))

        await write_file_atomic(path_obj, final_content)
        return await compute_checksum(final_content)

    except Exception as e:  # pragma: no cover
        logger.error(
            "Failed to update frontmatter",
            path=str(path) if isinstance(path, (str, Path)) else "<unknown>",
            error=str(e),
        )
        raise FileError(f"Failed to update frontmatter: {e}")
