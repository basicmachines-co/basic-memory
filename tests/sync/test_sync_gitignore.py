"""Tests for sync service gitignore integration."""

import pytest
from pathlib import Path

from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_sync_respects_gitignore(
    sync_service: SyncService,
    project_config,
    entity_service,
    test_files,
):
    """Test that sync operation respects gitignore patterns."""
    project_dir = project_config.home

    # Create a .gitignore file
    gitignore_content = """
# Build output
build/
dist/
*.o

# Dependencies
node_modules/

# IDE files
.vscode/
"""
    await create_test_file(project_dir / ".gitignore", gitignore_content)

    # Create test files that should be ignored
    ignored_files = {
        "build/app": "binary",
        "dist/bundle.js": "bundle",
        "lib/helper.o": "object file",
        "web/node_modules/package/index.js": "package",
        ".vscode/settings.json": "settings",
    }

    # Create test files that should be synced
    synced_files = {
        "src/app.js": "source code",
        "docs/README.md": "documentation",
        "config.json": "configuration",
    }

    # Create all test files
    for files in [ignored_files, synced_files]:
        for path, content in files.items():
            await create_test_file(project_dir / path, content)

    # Run sync
    report = await sync_service.sync(project_dir)

    # Check that only non-ignored files were synced
    for file in synced_files:
        assert any(file in p for p in report.new), f"File {file} should have been synced"

    # Check that ignored files were not synced
    for file in ignored_files:
        assert not any(file in p for p in report.new), f"File {file} should have been ignored"

    # Verify entities were created only for non-ignored files
    entities = await entity_service.repository.find_all()
    entity_paths = [e.file_path for e in entities]

    for file in synced_files:
        assert any(file in p for p in entity_paths), f"Entity should exist for {file}"

    for file in ignored_files:
        assert not any(file in p for p in entity_paths), f"Entity should not exist for {file}"


@pytest.mark.asyncio
async def test_sync_respects_default_ignores(
    sync_service: SyncService,
    project_config,
    entity_service,
):
    """Test that sync operation respects default ignore patterns even without .gitignore."""
    project_dir = project_config.home

    # Create test files that should be ignored by default patterns
    ignored_files = {
        "target/debug/app": "binary",
        "__pycache__/module.pyc": "python cache",
        "venv/lib/python3.8": "virtualenv",
        ".git/HEAD": "git file",
    }

    # Create test files that should be synced
    synced_files = {
        "src/main.py": "source code",
        "README.md": "documentation",
    }

    # Create all test files
    for files in [ignored_files, synced_files]:
        for path, content in files.items():
            await create_test_file(project_dir / path, content)

    # Run sync
    report = await sync_service.sync(project_dir)

    # Check that only non-ignored files were synced
    for file in synced_files:
        assert any(file in p for p in report.new), f"File {file} should have been synced"

    # Check that ignored files were not synced
    for file in ignored_files:
        assert not any(file in p for p in report.new), f"File {file} should have been ignored"

    # Verify entities were created only for non-ignored files
    entities = await entity_service.repository.find_all()
    entity_paths = [e.file_path for e in entities]

    for file in synced_files:
        assert any(file in p for p in entity_paths), f"Entity should exist for {file}"

    for file in ignored_files:
        assert not any(file in p for p in entity_paths), f"Entity should not exist for {file}"
