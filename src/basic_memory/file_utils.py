"""Utilities for file operations."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, Union

import aiofiles
import yaml
import frontmatter
from loguru import logger

from basic_memory.utils import FilePath


@dataclass
class FileMetadata:
    """File metadata for cloud-compatible file operations.

    This dataclass provides a cloud-agnostic way to represent file metadata,
    enabling S3FileService to return metadata from head_object responses
    instead of mock stat_result with zeros.
    """

    size: int
    created_at: datetime
    modified_at: datetime


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


async def write_file_atomic(path: FilePath, content: str) -> None:
    """
    Write file with atomic operation using temporary file.

    Uses aiofiles for true async I/O (non-blocking).

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
        # Use aiofiles for non-blocking write
        async with aiofiles.open(temp_path, mode="w", encoding="utf-8") as f:
            await f.write(content)

        # Atomic rename (this is fast, doesn't need async)
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


def dump_frontmatter(post: frontmatter.Post) -> str:
    """
    Serialize frontmatter.Post to markdown with Obsidian-compatible YAML format.

    This function ensures that:
    1. Tags are formatted as YAML lists instead of JSON arrays
    2. String values are properly quoted to handle special characters (colons, etc.)

    Good (Obsidian compatible):
    ---
    title: "L2 Governance Core (Split: Core)"
    tags:
    - system
    - overview
    - reference
    ---

    Bad (causes parsing errors):
    ---
    title: L2 Governance Core (Split: Core)  # Unquoted colon breaks YAML
    tags: ["system", "overview", "reference"]
    ---

    Args:
        post: frontmatter.Post object to serialize

    Returns:
        String containing markdown with properly formatted YAML frontmatter
    """
    if not post.metadata:
        # No frontmatter, just return content
        return post.content

    # Serialize YAML with block style for lists
    # SafeDumper automatically quotes values with special characters (colons, etc.)
    yaml_str = yaml.dump(
        post.metadata,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        Dumper=yaml.SafeDumper,
    )

    # Construct the final markdown with frontmatter
    if post.content:
        return f"---\n{yaml_str}---\n\n{post.content}"
    else:
        return f"---\n{yaml_str}---\n"


def sanitize_for_filename(text: str, replacement: str = "-") -> str:
    """
    Sanitize string to be safe for use as a note title
    Replaces path separators and other problematic characters
    with hyphens.
    """
    # replace both POSIX and Windows path separators
    text = re.sub(r"[/\\]", replacement, text)

    # replace some other problematic chars
    text = re.sub(r'[<>:"|?*]', replacement, text)

    # compress multiple, repeated replacements
    text = re.sub(f"{re.escape(replacement)}+", replacement, text)

    return text.strip(replacement)


def sanitize_for_folder(folder: str) -> str:
    """
    Sanitize folder path to be safe for use in file system paths.
    Removes leading/trailing whitespace, compresses multiple slashes,
    and removes special characters except for /, -, and _.
    """
    if not folder:
        return ""

    sanitized = folder.strip()

    if sanitized.startswith("./"):
        sanitized = sanitized[2:]

    # ensure no special characters (except for a few that are allowed)
    sanitized = "".join(
        c for c in sanitized if c.isalnum() or c in (".", " ", "-", "_", "\\", "/")
    ).rstrip()

    # compress multiple, repeated instances of path separators
    sanitized = re.sub(r"[\\/]+", "/", sanitized)

    # trim any leading/trailing path separators
    sanitized = sanitized.strip("\\/")

    return sanitized
