"""Utilities for file operations."""

import hashlib
from pathlib import Path
from typing import Dict, Any

import yaml
from loguru import logger


class FileError(Exception):
    """Base exception for file operations."""

    pass


class FileWriteError(FileError):
    """Raised when file operations fail."""

    pass


class ParseError(FileError):
    """Raised when parsing file content fails."""

    pass


async def compute_checksum(content: str) -> str:
    """
    Compute SHA-256 checksum of content.

    Args:
        content: Text content to hash

    Returns:
        SHA-256 hex digest

    Raises:
        FileError: If checksum computation fails
    """
    try:
        return hashlib.sha256(content.encode()).hexdigest()
    except Exception as e:  # pragma: no cover
        logger.error(f"Failed to compute checksum: {e}")
        raise FileError(f"Failed to compute checksum: {e}")


async def ensure_directory(path: Path) -> None:
    """
    Ensure directory exists, creating if necessary.

    Args:
        path: Directory path to ensure

    Raises:
        FileWriteError: If directory creation fails
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # pragma: no cover
        logger.error(f"Failed to create directory: {path}: {e}")
        raise FileWriteError(f"Failed to create directory {path}: {e}")


async def write_file_atomic(path: Path, content: str) -> None:
    """
    Write file with atomic operation using temporary file.

    Args:
        path: Target file path
        content: Content to write

    Raises:
        FileWriteError: If write operation fails
    """
    temp_path = path.with_suffix(".tmp")
    try:
        temp_path.write_text(content)
        temp_path.replace(path)
        logger.debug(f"wrote file: {path}")
    except Exception as e:  # pragma: no cover
        temp_path.unlink(missing_ok=True)
        logger.error(f"Failed to write file: {path}: {e}")
        raise FileWriteError(f"Failed to write file {path}: {e}")


def has_frontmatter(content: str) -> bool:
    """
    Check if content contains valid YAML frontmatter.

    Args:
        content: Content to check

    Returns:
        True if content has valid frontmatter markers (---), False otherwise
    """
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


async def update_frontmatter(path: Path, updates: Dict[str, Any]) -> str:
    """Update frontmatter fields in a file while preserving all content.

    Only modifies the frontmatter section, leaving all content untouched.
    Creates frontmatter section if none exists.
    Returns checksum of updated file.

    Args:
        path: Path to markdown file
        updates: Dict of frontmatter fields to update

    Returns:
        Checksum of updated file

    Raises:
        FileError: If file operations fail
        ParseError: If frontmatter parsing fails
    """
    try:
        # Read current content
        content = path.read_text()

        # Parse current frontmatter
        current_fm = {}
        if has_frontmatter(content):
            current_fm = parse_frontmatter(content)
            content = remove_frontmatter(content)

        # Update frontmatter
        new_fm = {**current_fm, **updates}

        # Write new file with updated frontmatter
        yaml_fm = yaml.dump(new_fm, sort_keys=False)
        final_content = f"---\n{yaml_fm}---\n\n{content.strip()}"

        await write_file_atomic(path, final_content)
        return await compute_checksum(final_content)

    except Exception as e:  # pragma: no cover
        logger.error(f"Failed to update frontmatter in {path}: {e}")
        raise FileError(f"Failed to update frontmatter: {e}")
