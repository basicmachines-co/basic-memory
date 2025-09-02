"""Gitignore pattern handling."""

import os
from pathlib import Path
from typing import List, Optional

import pathspec


def get_gitignore_patterns(project_root: Path) -> List[str]:
    """Get gitignore patterns from .gitignore file.
    
    Args:
        project_root: Root directory containing .gitignore
        
    Returns:
        List of gitignore pattern strings
    """
    gitignore_path = project_root / ".gitignore"
    patterns = []
    
    # Default ignore patterns for build artifacts
    DEFAULT_PATTERNS = [
        # Build directories
        'target/',        # Rust
        'node_modules/',  # Node.js
        'dist/',         # Various build systems
        'build/',        # Various build systems
        '__pycache__/',  # Python
        '.pytest_cache/', # Python tests
        '.ruff_cache/',  # Ruff linter
        '.mypy_cache/',  # MyPy type checker
        '.coverage/',    # Python coverage
        
        # Binary and object files
        '*.o',          # Object files
        '*.so',         # Shared objects
        '*.dylib',      # Dynamic libraries
        '*.dll',        # Windows DLLs
        '*.pyc',        # Python bytecode
        '*.pyo',        # Python optimized bytecode
        '*.pyd',        # Python DLLs
        
        # IDE and editor files
        '.idea/',       # IntelliJ
        '.vscode/',     # VS Code
        '*.swp',        # Vim swap files
        '.DS_Store',    # macOS metadata
        
        # Environment and dependency directories
        '.venv/',       # Python venvs
        'venv/',        # Python venvs
        'env/',         # Python venvs
        '.env/',        # Environment files
        'site-packages/', # Python packages
        
        # Version control
        '.git/',        # Git directory
        '.gitmodules',  # Git submodules
    ]
    
    patterns.extend(DEFAULT_PATTERNS)
    
    if gitignore_path.exists():
        with open(gitignore_path) as f:
            # Add each non-empty line that doesn't start with #
            patterns.extend(
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            )
            
    return patterns

def build_gitignore_spec(project_root: Path) -> pathspec.PathSpec:
    """Build a PathSpec object from gitignore patterns.
    
    Args:
        project_root: Root directory containing .gitignore
        
    Returns:
        PathSpec object for matching paths
    """
    patterns = get_gitignore_patterns(project_root)
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

def should_ignore_file(file_path: str, project_root: Path) -> bool:
    """Check if a file should be ignored based on gitignore patterns.
    
    Args:
        file_path: Path to the file to check
        project_root: Root directory containing .gitignore
        
    Returns:
        True if the file should be ignored, False otherwise
    """
    spec = build_gitignore_spec(project_root)
    
    # Get the relative path from the project root
    try:
        relative_path = Path(file_path).relative_to(project_root)
    except ValueError:
        # If the path is not relative to the project root, don't ignore it
        return False
        
    # Check if the file matches any gitignore pattern
    return spec.match_file(str(relative_path))
