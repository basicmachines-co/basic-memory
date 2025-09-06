"""Tests for gitignore pattern handling."""

from pathlib import Path

import pytest

from basic_memory.file_utils.gitignore import should_ignore_file, get_gitignore_patterns


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_gitignore_basic_patterns(tmp_path):
    """Test that basic gitignore patterns are respected."""
    # Create a test project structure
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create a .gitignore file
    gitignore_content = """
# Build artifacts
target/
build/
*.o
*.so
node_modules/

# IDE files
.vscode/
.idea/
"""
    await create_test_file(project_dir / ".gitignore", gitignore_content)

    # Create some test files
    test_files = {
        # Files that should be ignored
        "target/debug/app": "binary",
        "build/lib.o": "object file",
        "node_modules/package/index.js": "js file",
        ".vscode/settings.json": "vscode settings",
        # Files that should not be ignored
        "src/app.rs": "source code",
        "docs/README.md": "documentation",
    }

    for path, content in test_files.items():
        await create_test_file(project_dir / path, content)

    # Test each file
    should_ignore = ["target/debug/app", "build/lib.o", "node_modules/package/index.js", ".vscode/settings.json"]
    should_not_ignore = ["src/app.rs", "docs/README.md"]

    for file in should_ignore:
        file_path = project_dir / file
        assert should_ignore_file(str(file_path), project_dir), f"Should ignore {file}"

    for file in should_not_ignore:
        file_path = project_dir / file
        assert not should_ignore_file(str(file_path), project_dir), f"Should not ignore {file}"


@pytest.mark.asyncio
async def test_gitignore_default_patterns(tmp_path):
    """Test that default ignore patterns are applied even without .gitignore file."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create test files matching default patterns
    test_files = {
        # Files that should be ignored by default patterns
        "target/release/app": "binary",
        "__pycache__/module.pyc": "python cache",
        "venv/lib/python3.8": "virtualenv",
        ".git/HEAD": "git file",
        # Files that should not be ignored
        "src/main.py": "source code",
        "README.md": "documentation",
    }

    for path, content in test_files.items():
        await create_test_file(project_dir / path, content)

    # Test each file
    should_ignore = ["target/release/app", "__pycache__/module.pyc", "venv/lib/python3.8", ".git/HEAD"]
    should_not_ignore = ["src/main.py", "README.md"]

    for file in should_ignore:
        file_path = project_dir / file
        assert should_ignore_file(str(file_path), project_dir), f"Should ignore {file}"

    for file in should_not_ignore:
        file_path = project_dir / file
        assert not should_ignore_file(str(file_path), project_dir), f"Should not ignore {file}"


@pytest.mark.asyncio
async def test_gitignore_complex_patterns(tmp_path):
    """Test that complex gitignore patterns are handled correctly."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create a .gitignore file with complex patterns
    gitignore_content = """
# Nested wildcards
**/node_modules/**
**/build-*/

# Negation
*.log
!important.log

# Multiple extensions
*.tar.gz
*.tar.bz2

# Specific file in any directory
**/temp.txt
"""
    await create_test_file(project_dir / ".gitignore", gitignore_content)

    # Create test files
    test_files = {
        # Should be ignored
        "web/node_modules/package.json": "package",
        "build-debug/app": "debug build",
        "logs/error.log": "error log",
        "src/temp.txt": "temp file",
        "archive.tar.gz": "archive",
        # Should not be ignored
        "important.log": "important log",
        "src/app.js": "source code",
    }

    for path, content in test_files.items():
        await create_test_file(project_dir / path, content)

    # Test each file
    should_ignore = [
        "web/node_modules/package.json",
        "build-debug/app",
        "logs/error.log",
        "src/temp.txt",
        "archive.tar.gz",
    ]
    should_not_ignore = ["important.log", "src/app.js"]

    for file in should_ignore:
        file_path = project_dir / file
        assert should_ignore_file(str(file_path), project_dir), f"Should ignore {file}"

    for file in should_not_ignore:
        file_path = project_dir / file
        assert not should_ignore_file(str(file_path), project_dir), f"Should not ignore {file}"


@pytest.mark.asyncio
async def test_gitignore_pattern_loading(tmp_path):
    """Test that gitignore patterns are correctly loaded from file."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create a .gitignore file
    gitignore_content = """
# Comment
*.pyc
/dist/
/build/

# Empty lines should be ignored

node_modules/
# Another comment
.env
"""
    await create_test_file(project_dir / ".gitignore", gitignore_content)

    # Load patterns
    patterns = get_gitignore_patterns(project_dir)

    # Check that patterns were loaded correctly
    assert "*.pyc" in patterns
    assert "/dist/" in patterns
    assert "/build/" in patterns
    assert "node_modules/" in patterns
    assert ".env" in patterns

    # Check that comments and empty lines were ignored
    assert "# Comment" not in patterns
    assert "" not in patterns
    assert "# Another comment" not in patterns

    # Verify default patterns are included
    assert "target/" in patterns
    assert "*.o" in patterns
    assert ".git/" in patterns
