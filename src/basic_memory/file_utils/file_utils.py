"""File utility functions."""

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from unidecode import unidecode

import frontmatter
from loguru import logger

class FileError(Exception):
    """Base class for file-related errors."""

class FileWriteError(FileError):
    """Error writing to a file."""

class ParseError(FileError):
    """Error parsing file contents."""

def ensure_directory(path: Union[str, Path]) -> None:
    """Create directory if it doesn't exist.
    
    Args:
        path: Path to directory to create
    """
    Path(path).mkdir(parents=True, exist_ok=True)

async def compute_checksum(path: str) -> str:
    """Compute SHA-256 checksum of file contents.
    
    Args:
        path: Path to file
        
    Returns:
        Hex digest of SHA-256 hash
    """
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def write_file_atomic(path: Union[str, Path], content: str) -> None:
    """Write file atomically using a temporary file.
    
    Args:
        path: Path to write to
        content: Content to write
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create temp file in same directory
    fd, temp_path = tempfile.mkstemp(dir=str(path.parent))
    success = False
    
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        # Atomic rename
        Path(temp_path).rename(path)
        success = True
    finally:
        if not success:
            try:
                Path(temp_path).unlink()
            except OSError:
                pass
            
def has_frontmatter(content: str) -> bool:
    """Check if content has frontmatter.
    
    Args:
        content: File content to check
        
    Returns:
        True if content has frontmatter
    """
    return content.startswith('---\n')

def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse frontmatter from content.
    
    Args:
        content: Content to parse
        
    Returns:
        Tuple of (frontmatter dict, remaining content)
        
    Raises:
        ParseError: If frontmatter is invalid
    """
    try:
        post = frontmatter.loads(content)
        return dict(post.metadata), post.content
    except Exception as e:
        logger.error(f"Error parsing frontmatter: {e}")
        raise ParseError(f"Invalid frontmatter: {e}") from e

def remove_frontmatter(content: str) -> str:
    """Remove frontmatter from content if present.
    
    Args:
        content: Content to process
        
    Returns:
        Content with frontmatter removed
    """
    if not has_frontmatter(content):
        return content
        
    try:
        _, content = parse_frontmatter(content)
        return content
    except ParseError:
        return content

def sanitize_for_filename(text: str) -> str:
    """Convert text into a safe filename.
    
    Args:
        text: Text to sanitize
        
    Returns:
        Sanitized filename-safe string
    """
    # Convert unicode characters to ASCII
    text = unidecode(text)
    
    # Replace spaces with underscores and convert to lowercase
    text = text.lower().replace(' ', '_')
    
    # Remove any characters that aren't alphanumeric, dash, underscore or dot
    text = re.sub(r'[^a-z0-9\-_.]', '', text)
    
    # Remove any leading/trailing dots or spaces
    text = text.strip('. ')
    
    # Limit length and ensure it's not empty
    if not text:
        return 'unnamed'
    return text[:255]

def update_frontmatter(content: str, metadata: Dict[str, Any]) -> str:
    """Update or add frontmatter to content.
    
    Args:
        content: Content to update
        metadata: New metadata to set
        
    Returns:
        Updated content with new frontmatter
        
    Raises:
        ParseError: If existing frontmatter is invalid
    """
    if has_frontmatter(content):
        try:
            existing_meta, content = parse_frontmatter(content)
            # Update existing metadata
            existing_meta.update(metadata)
            metadata = existing_meta
        except ParseError as e:
            logger.warning(f"Error parsing existing frontmatter, will overwrite: {e}")
            content = remove_frontmatter(content)
            
    # Create new frontmatter block
    meta_lines = ['---']
    for key, value in metadata.items():
        meta_lines.append(f"{key}: {value}")
    meta_lines.append('---\n')
    
    return '\n'.join(meta_lines) + content
