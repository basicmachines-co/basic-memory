"""Test for issue #72 - notes with wikilinks staying in modified status."""

from pathlib import Path

import pytest

from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str) -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def force_full_scan(sync_service: SyncService) -> None:
    """Force next sync to do a full scan by clearing watermark (for testing moves/deletions)."""
    if sync_service.entity_repository.project_id is not None:
        project = await sync_service.project_repository.find_by_id(
            sync_service.entity_repository.project_id
        )
        if project:
            await sync_service.project_repository.update(
                project.id,
                {
                    "last_scan_timestamp": None,
                    "last_file_count": None,
                },
            )


@pytest.mark.asyncio
async def test_wikilink_modified_status_issue(sync_service: SyncService, project_config):
    """Test that files with wikilinks don't remain in modified status after sync."""
    project_dir = project_config.home

    # Create a file with a wikilink
    content = """---
title: Test Wikilink
type: note
---
# Test File

This file contains a wikilink to [[another-file]].
"""
    test_file_path = project_dir / "test_wikilink.md"
    await create_test_file(test_file_path, content)

    # Initial sync
    report1 = await sync_service.sync(project_config.home)
    assert "test_wikilink.md" in report1.new
    assert "test_wikilink.md" not in report1.modified

    # Sync again without changing the file - should not be modified
    report2 = await sync_service.sync(project_config.home)
    assert "test_wikilink.md" not in report2.new
    assert "test_wikilink.md" not in report2.modified

    # Create the target file
    target_content = """---
title: Another File
type: note
---
# Another File

This is the target file.
"""
    target_file_path = project_dir / "another_file.md"
    await create_test_file(target_file_path, target_content)

    # Force full scan to detect the new file
    # (file just created may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Sync again after adding target file
    report3 = await sync_service.sync(project_config.home)
    assert "another_file.md" in report3.new
    assert "test_wikilink.md" not in report3.modified

    # Sync one more time - both files should now be stable
    report4 = await sync_service.sync(project_config.home)
    assert "test_wikilink.md" not in report4.modified
    assert "another_file.md" not in report4.modified
