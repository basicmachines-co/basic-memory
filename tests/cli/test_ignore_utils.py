"""Tests for ignore_utils module."""

import tempfile
from pathlib import Path

from basic_memory.ignore_utils import (
    DEFAULT_IGNORE_PATTERNS,
    load_gitignore_patterns,
    should_ignore_path,
    filter_files,
)


def test_load_default_patterns_only():
    """Test loading default patterns when no .gitignore exists."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        patterns = load_gitignore_patterns(temp_path)

        # Should include all default patterns
        assert DEFAULT_IGNORE_PATTERNS.issubset(patterns)
        # Should only have default patterns (no custom ones)
        assert patterns == DEFAULT_IGNORE_PATTERNS


def test_load_patterns_with_gitignore():
    """Test loading patterns from .gitignore file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a .gitignore file
        gitignore_content = """
# Python
*.pyc
__pycache__/

# Node
node_modules/
*.log

# Custom
secrets/
temp_*
"""
        (temp_path / ".gitignore").write_text(gitignore_content)

        patterns = load_gitignore_patterns(temp_path)

        # Should include default patterns
        assert DEFAULT_IGNORE_PATTERNS.issubset(patterns)

        # Should include custom patterns from .gitignore
        assert "*.pyc" in patterns
        assert "__pycache__/" in patterns
        assert "node_modules/" in patterns
        assert "*.log" in patterns
        assert "secrets/" in patterns
        assert "temp_*" in patterns

        # Should skip comments and empty lines
        assert "# Python" not in patterns
        assert "# Node" not in patterns
        assert "# Custom" not in patterns


def test_load_patterns_empty_gitignore():
    """Test loading patterns with empty .gitignore file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create empty .gitignore file
        (temp_path / ".gitignore").write_text("")

        patterns = load_gitignore_patterns(temp_path)

        # Should only have default patterns
        assert patterns == DEFAULT_IGNORE_PATTERNS


def test_load_patterns_unreadable_gitignore():
    """Test graceful handling of unreadable .gitignore file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create .gitignore file with restricted permissions
        gitignore_file = temp_path / ".gitignore"
        gitignore_file.write_text("*.log")
        gitignore_file.chmod(0o000)  # No read permissions

        try:
            patterns = load_gitignore_patterns(temp_path)

            # On Windows, chmod might not work as expected, so we need to check
            # if the file is actually unreadable
            try:
                with gitignore_file.open("r"):
                    pass
                # If we can read it, the test environment doesn't support this scenario
                # In this case, the patterns should include *.log
                assert "*.log" in patterns
            except (PermissionError, OSError):
                # File is actually unreadable, should fallback to default patterns only
                assert patterns == DEFAULT_IGNORE_PATTERNS
                assert "*.log" not in patterns
        finally:
            # Restore permissions for cleanup
            gitignore_file.chmod(0o644)


def test_should_ignore_default_patterns():
    """Test ignoring files matching default patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        patterns = DEFAULT_IGNORE_PATTERNS

        test_cases = [
            # Git directory
            (temp_path / ".git" / "config", True),
            # Python artifacts
            (temp_path / "main.pyc", True),
            (temp_path / "__pycache__" / "main.cpython-39.pyc", True),
            (temp_path / "src" / "__pycache__" / "module.pyc", True),
            # Virtual environments
            (temp_path / ".venv" / "lib" / "python.so", True),
            (temp_path / "venv" / "bin" / "python", True),
            (temp_path / "env" / "lib" / "site-packages", True),
            # Node.js
            (temp_path / "node_modules" / "package" / "index.js", True),
            # IDE files
            (temp_path / ".idea" / "workspace.xml", True),
            (temp_path / ".vscode" / "settings.json", True),
            # OS files
            (temp_path / ".DS_Store", True),
            (temp_path / "Thumbs.db", True),
            # Valid files that should NOT be ignored
            (temp_path / "main.py", False),
            (temp_path / "README.md", False),
            (temp_path / "src" / "module.py", False),
            (temp_path / "package.json", False),
        ]

        for file_path, should_be_ignored in test_cases:
            result = should_ignore_path(file_path, temp_path, patterns)
            assert result == should_be_ignored, (
                f"Failed for {file_path}: expected {should_be_ignored}, got {result}"
            )


def test_should_ignore_glob_patterns():
    """Test glob pattern matching."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        patterns = {"*.log", "temp_*", "test*.txt"}

        test_cases = [
            (temp_path / "debug.log", True),
            (temp_path / "app.log", True),
            (temp_path / "sub" / "error.log", True),
            (temp_path / "temp_file.txt", True),
            (temp_path / "temp_123", True),
            (temp_path / "test_data.txt", True),
            (temp_path / "testfile.txt", True),
            (temp_path / "app.txt", False),
            (temp_path / "file.py", False),
            (temp_path / "data.json", False),
        ]

        for file_path, should_be_ignored in test_cases:
            result = should_ignore_path(file_path, temp_path, patterns)
            assert result == should_be_ignored, f"Failed for {file_path}"


def test_should_ignore_directory_patterns():
    """Test directory pattern matching (ending with /)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        patterns = {"build/", "dist/", "logs/"}

        test_cases = [
            (temp_path / "build" / "output.js", True),
            (temp_path / "dist" / "main.css", True),
            (temp_path / "logs" / "app.log", True),
            (temp_path / "src" / "build" / "file.js", True),  # Nested
            (temp_path / "build.py", False),  # File with same name
            (temp_path / "build_script.sh", False),  # Similar name
            (temp_path / "src" / "main.py", False),  # Different directory
        ]

        for file_path, should_be_ignored in test_cases:
            result = should_ignore_path(file_path, temp_path, patterns)
            assert result == should_be_ignored, f"Failed for {file_path}"


def test_should_ignore_root_relative_patterns():
    """Test patterns starting with / (root relative)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        patterns = {"/config.txt", "/build/", "/tmp/*.log"}

        test_cases = [
            (temp_path / "config.txt", True),  # Root level
            (temp_path / "build" / "app.js", True),  # Root level directory
            (temp_path / "tmp" / "debug.log", True),  # Root level with glob
            (temp_path / "src" / "config.txt", False),  # Not at root
            (temp_path / "project" / "build" / "file.js", False),  # Not at root
            (temp_path / "data" / "tmp" / "app.log", False),  # Not at root
        ]

        for file_path, should_be_ignored in test_cases:
            result = should_ignore_path(file_path, temp_path, patterns)
            assert result == should_be_ignored, f"Failed for {file_path}"


def test_should_ignore_invalid_relative_path():
    """Test handling of paths that cannot be made relative to base."""
    patterns = {"*.pyc"}

    # File outside of base path should not be ignored
    base_path = Path("/tmp/project")
    file_path = Path("/home/user/file.pyc")

    result = should_ignore_path(file_path, base_path, patterns)
    assert result is False


def test_filter_files_with_patterns():
    """Test filtering files with given patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create test files
        files = [
            temp_path / "main.py",
            temp_path / "main.pyc",
            temp_path / "__pycache__" / "module.pyc",
            temp_path / "README.md",
            temp_path / ".git" / "config",
            temp_path / "package.json",
        ]

        # Ensure parent directories exist
        for file_path in files:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("test content")

        patterns = {"*.pyc", "__pycache__", ".git"}
        filtered_files, ignored_count = filter_files(files, temp_path, patterns)

        # Should keep valid files
        expected_kept = [
            temp_path / "main.py",
            temp_path / "README.md",
            temp_path / "package.json",
        ]

        assert len(filtered_files) == 3
        assert set(filtered_files) == set(expected_kept)
        assert ignored_count == 3  # main.pyc, module.pyc, config


def test_filter_files_no_patterns():
    """Test filtering with no patterns (should keep all files)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        files = [
            temp_path / "main.py",
            temp_path / "main.pyc",
            temp_path / "README.md",
        ]

        patterns = set()
        filtered_files, ignored_count = filter_files(files, temp_path, patterns)

        assert len(filtered_files) == 3
        assert set(filtered_files) == set(files)
        assert ignored_count == 0


def test_filter_files_with_gitignore_loading():
    """Test filtering with automatic .gitignore loading."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create .gitignore
        gitignore_content = """
*.log
temp_*
"""
        (temp_path / ".gitignore").write_text(gitignore_content)

        # Create test files
        files = [
            temp_path / "app.py",
            temp_path / "debug.log",
            temp_path / "temp_file.txt",
            temp_path / "README.md",
        ]

        # Ensure files exist
        for file_path in files:
            file_path.write_text("test content")

        filtered_files, ignored_count = filter_files(files, temp_path)  # patterns=None

        # Should ignore .log files and temp_* files, plus default patterns
        expected_kept = [temp_path / "app.py", temp_path / "README.md"]

        assert len(filtered_files) == 2
        assert set(filtered_files) == set(expected_kept)
        assert ignored_count == 2  # debug.log, temp_file.txt
