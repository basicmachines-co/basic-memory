"""Basic memory file utilities."""

from .gitignore import should_ignore_file, get_gitignore_patterns, build_gitignore_spec
from .file_utils import (
    FileError,
    FileWriteError,
    ParseError,
    compute_checksum,
    ensure_directory,
    has_frontmatter,
    parse_frontmatter,
    remove_frontmatter,
    update_frontmatter,
    write_file_atomic,
)

__all__ = [
    "FileError",
    "FileWriteError",
    "ParseError",
    "compute_checksum",
    "ensure_directory",
    "has_frontmatter",
    "parse_frontmatter",
    "remove_frontmatter",
    "update_frontmatter",
    "write_file_atomic",
    "should_ignore_file",
    "get_gitignore_patterns", 
    "build_gitignore_spec",
]
