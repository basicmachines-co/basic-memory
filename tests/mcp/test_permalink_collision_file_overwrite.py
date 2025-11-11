"""Tests for permalink collision file overwrite bug discovered in live testing.

This test reproduces a critical data loss bug where creating notes with
titles that normalize to different permalinks but resolve to the same
file location causes silent file overwrites without warning.

Related to GitHub Issue #139 but tests a different aspect - not database
UNIQUE constraints, but actual file overwrite behavior.

Example scenario from live testing:
1. Create "Node A" → file: edge-cases/Node A.md, permalink: edge-cases/node-a
2. Create "Node C" → file: edge-cases/Node C.md, permalink: edge-cases/node-c
3. BUG: Node C creation overwrites edge-cases/Node A.md file content
4. Result: File "Node A.md" exists but contains "Node C" content
"""

import pytest
from pathlib import Path
from textwrap import dedent

from basic_memory.mcp.tools import write_note, read_note
from basic_memory.sync.sync_service import SyncService
from basic_memory.config import ProjectConfig
from basic_memory.services import EntityService


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
async def test_permalink_collision_should_not_overwrite_different_file(app, test_project):
    """Test that creating notes with different titles doesn't overwrite existing files.

    This test reproduces the critical bug discovered in Phase 4 of live testing where:
    - Creating "Node A" worked fine
    - Creating "Node C" silently overwrote Node A.md's content
    - No warning or error was shown to the user
    - Original Node A content was permanently lost

    Expected behavior:
    - Each note with a different title should create/update its own file
    - No silent overwrites should occur
    - Files should maintain their distinct content

    Current behavior (BUG):
    - Second note creation sometimes overwrites first note's file
    - File "Node A.md" contains "Node C" content after creating Node C
    - Data loss occurs without user warning
    """
    # Step 1: Create first note "Node A"
    result_a = await write_note.fn(
        project=test_project.name,
        title="Node A",
        folder="edge-cases",
        content="# Node A\n\nOriginal content for Node A\n\n## Relations\n- links_to [[Node B]]",
    )

    assert "# Created note" in result_a
    assert "file_path: edge-cases/Node A.md" in result_a
    assert "permalink: edge-cases/node-a" in result_a

    # Verify Node A content via read
    content_a = await read_note.fn("edge-cases/node-a", project=test_project.name)
    assert "Node A" in content_a
    assert "Original content for Node A" in content_a

    # Step 2: Create second note "Node B" (should be independent)
    result_b = await write_note.fn(
        project=test_project.name,
        title="Node B",
        folder="edge-cases",
        content="# Node B\n\nContent for Node B",
    )

    assert "# Created note" in result_b
    assert "file_path: edge-cases/Node B.md" in result_b
    assert "permalink: edge-cases/node-b" in result_b

    # Step 3: Create third note "Node C" (this is where the bug occurs)
    result_c = await write_note.fn(
        project=test_project.name,
        title="Node C",
        folder="edge-cases",
        content="# Node C\n\nContent for Node C\n\n## Relations\n- links_to [[Node A]]",
    )

    assert "# Created note" in result_c
    assert "file_path: edge-cases/Node C.md" in result_c
    assert "permalink: edge-cases/node-c" in result_c

    # CRITICAL CHECK: Verify Node A still has its original content
    # This is where the bug manifests - Node A.md gets overwritten with Node C content
    content_a_after = await read_note.fn("edge-cases/node-a", project=test_project.name)
    assert "Node A" in content_a_after, "Node A title should still be 'Node A'"
    assert "Original content for Node A" in content_a_after, (
        "Node A file should NOT be overwritten by Node C creation"
    )
    assert "Content for Node C" not in content_a_after, "Node A should NOT contain Node C's content"

    # Verify Node C has its own content
    content_c = await read_note.fn("edge-cases/node-c", project=test_project.name)
    assert "Node C" in content_c
    assert "Content for Node C" in content_c
    assert "Original content for Node A" not in content_c, (
        "Node C should not contain Node A's content"
    )

    # Verify files physically exist with correct content
    project_path = Path(test_project.path)
    node_a_file = project_path / "edge-cases" / "Node A.md"
    node_c_file = project_path / "edge-cases" / "Node C.md"

    assert node_a_file.exists(), "Node A.md file should exist"
    assert node_c_file.exists(), "Node C.md file should exist"

    # Read actual file contents to verify no overwrite occurred
    node_a_file_content = node_a_file.read_text()
    node_c_file_content = node_c_file.read_text()

    assert "Node A" in node_a_file_content, "Physical file Node A.md should contain Node A title"
    assert "Original content for Node A" in node_a_file_content, (
        "Physical file Node A.md should contain original Node A content"
    )
    assert "Content for Node C" not in node_a_file_content, (
        "Physical file Node A.md should NOT contain Node C content"
    )

    assert "Node C" in node_c_file_content, "Physical file Node C.md should contain Node C title"
    assert "Content for Node C" in node_c_file_content, (
        "Physical file Node C.md should contain Node C content"
    )


@pytest.mark.asyncio
async def test_notes_with_similar_titles_maintain_separate_files(app, test_project):
    """Test that notes with similar titles that normalize differently don't collide.

    Tests additional edge cases around permalink normalization to ensure
    we don't have collision issues with various title patterns.
    """
    # Create notes with titles that could potentially cause issues
    titles_and_folders = [
        ("My Note", "test"),
        ("My-Note", "test"),  # Different title, similar permalink
        ("My_Note", "test"),  # Underscore vs hyphen
        ("my note", "test"),  # Case variation
    ]

    created_permalinks = []

    for title, folder in titles_and_folders:
        result = await write_note.fn(
            project=test_project.name,
            title=title,
            folder=folder,
            content=f"# {title}\n\nUnique content for {title}",
        )

        permalink = None
        # Extract permalink from result
        for line in result.split("\n"):
            if line.startswith("permalink:"):
                permalink = line.split(":", 1)[1].strip()
                created_permalinks.append((title, permalink))
                break

        # Verify each note can be read back with its own content
        content = await read_note.fn(permalink, project=test_project.name)
        assert f"Unique content for {title}" in content, (
            f"Note with title '{title}' should maintain its unique content"
        )

    # Verify all created permalinks are tracked
    assert len(created_permalinks) == len(titles_and_folders), (
        "All notes should be created successfully"
    )


@pytest.mark.asyncio
async def test_sequential_note_creation_preserves_all_files(app, test_project):
    """Test that rapid sequential note creation doesn't cause file overwrites.

    This test creates multiple notes in sequence to ensure that file
    creation/update logic doesn't have race conditions or state issues
    that could cause overwrites.
    """
    notes_data = [
        ("Alpha", "# Alpha\n\nAlpha content"),
        ("Beta", "# Beta\n\nBeta content"),
        ("Gamma", "# Gamma\n\nGamma content"),
        ("Delta", "# Delta\n\nDelta content"),
        ("Epsilon", "# Epsilon\n\nEpsilon content"),
    ]

    # Create all notes
    for title, content in notes_data:
        result = await write_note.fn(
            project=test_project.name,
            title=title,
            folder="sequence-test",
            content=content,
        )
        assert "# Created note" in result or "# Updated note" in result

    # Verify all notes still exist with correct content
    for title, expected_content in notes_data:
        # Normalize title to permalink format
        permalink = f"sequence-test/{title.lower()}"
        content = await read_note.fn(permalink, project=test_project.name)

        assert title in content, f"Note '{title}' should still have its title"
        assert expected_content.split("\n\n")[1] in content, (
            f"Note '{title}' should still have its original content"
        )

    # Verify physical files exist
    project_path = Path(test_project.path)
    sequence_dir = project_path / "sequence-test"

    for title, _ in notes_data:
        file_path = sequence_dir / f"{title}.md"
        assert file_path.exists(), f"File for '{title}' should exist"

        file_content = file_path.read_text()
        assert title in file_content, f"Physical file for '{title}' should contain correct title"


@pytest.mark.asyncio
async def test_sync_permalink_collision_file_overwrite_bug(
    sync_service: SyncService,
    project_config: ProjectConfig,
    entity_service: EntityService,
):
    """Test that reproduces the permalink collision file overwrite bug via sync.

    This test directly creates files and runs sync to reproduce the exact bug
    discovered in live testing where Node C overwrote Node A.md.

    The bug occurs when:
    1. File "Node A.md" exists with permalink "edge-cases/node-a"
    2. File "Node C.md" is created with permalink "edge-cases/node-c"
    3. During sync, somehow Node C content overwrites Node A.md
    4. Result: File "Node A.md" contains Node C content (data loss!)
    """
    project_dir = project_config.home
    edge_cases_dir = project_dir / "edge-cases"
    edge_cases_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Create Node A file
    node_a_content = dedent("""
        ---
        title: Node A
        type: note
        tags:
        - circular-test
        ---

        # Node A

        Original content for Node A

        ## Relations
        - links_to [[Node B]]
        - references [[Node C]]
    """).strip()

    node_a_file = edge_cases_dir / "Node A.md"
    node_a_file.write_text(node_a_content)

    # Sync to create Node A in database
    await sync_service.sync(project_dir)

    # Verify Node A is in database
    node_a = await entity_service.get_by_permalink("edge-cases/node-a")
    assert node_a is not None
    assert node_a.title == "Node A"

    # Verify Node A file has correct content
    assert node_a_file.exists()
    node_a_file_content = node_a_file.read_text()
    assert "title: Node A" in node_a_file_content
    assert "Original content for Node A" in node_a_file_content

    # Step 2: Create Node B file
    node_b_content = dedent("""
        ---
        title: Node B
        type: note
        tags:
        - circular-test
        ---

        # Node B

        Content for Node B

        ## Relations
        - links_to [[Node C]]
        - part_of [[Node A]]
    """).strip()

    node_b_file = edge_cases_dir / "Node B.md"
    node_b_file.write_text(node_b_content)

    # Force full scan to detect the new file
    # (file just created may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Sync to create Node B
    await sync_service.sync(project_dir)

    # Step 3: Create Node C file (this is where the bug might occur)
    node_c_content = dedent("""
        ---
        title: Node C
        type: note
        tags:
        - circular-test
        ---

        # Node C

        Content for Node C

        ## Relations
        - links_to [[Node A]]
        - references [[Node B]]
    """).strip()

    node_c_file = edge_cases_dir / "Node C.md"
    node_c_file.write_text(node_c_content)

    # Force full scan to detect the new file
    # (file just created may not be newer than watermark due to timing precision)
    await force_full_scan(sync_service)

    # Sync to create Node C - THIS IS WHERE THE BUG OCCURS
    await sync_service.sync(project_dir)

    # CRITICAL VERIFICATION: Check if Node A file was overwritten
    assert node_a_file.exists(), "Node A.md file should still exist"

    # Read Node A file content to check for overwrite bug
    node_a_after_sync = node_a_file.read_text()

    # The bug: Node A.md contains Node C content instead of Node A content
    assert "title: Node A" in node_a_after_sync, (
        "Node A.md file should still have title: Node A in frontmatter"
    )
    assert "Node A" in node_a_after_sync, "Node A.md file should still contain 'Node A' title"
    assert "Original content for Node A" in node_a_after_sync, (
        f"Node A.md file should NOT be overwritten! Content: {node_a_after_sync[:200]}"
    )
    assert "Content for Node C" not in node_a_after_sync, (
        f"Node A.md should NOT contain Node C content! Content: {node_a_after_sync[:200]}"
    )

    # Verify Node C file exists with correct content
    assert node_c_file.exists(), "Node C.md file should exist"
    node_c_after_sync = node_c_file.read_text()
    assert "Node C" in node_c_after_sync
    assert "Content for Node C" in node_c_after_sync

    # Verify database has both entities correctly
    node_a_db = await entity_service.get_by_permalink("edge-cases/node-a")
    node_c_db = await entity_service.get_by_permalink("edge-cases/node-c")

    assert node_a_db is not None, "Node A should exist in database"
    assert node_a_db.title == "Node A", "Node A database entry should have correct title"

    assert node_c_db is not None, "Node C should exist in database"
    assert node_c_db.title == "Node C", "Node C database entry should have correct title"
