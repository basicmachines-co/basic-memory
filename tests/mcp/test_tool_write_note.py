"""Tests for note tools that exercise the full stack with SQLite."""

import re
from textwrap import dedent
from typing import Any

import pytest

from basic_memory import db
from basic_memory import config as config_module
from basic_memory.mcp.tools import write_note, read_note, delete_note
from basic_memory.mcp.tools.write_note import _compose_workspace_project_route
from basic_memory.repository.relation_repository import RelationRepository


def _without_managed_timestamps(markdown: str) -> str:
    """Drop BM's auto-managed created/modified frontmatter lines (#238/#684).

    Those timestamps are added to every note and are dynamic, so exact-content
    assertions strip them and match on the rest of the note. The timestamp
    behavior itself is covered by test_frontmatter_timestamps_integration.py.
    """
    return re.sub(r"^(?:created|modified): .*\n", "", markdown, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# _compose_workspace_project_route unit tests
# ---------------------------------------------------------------------------


def test_write_note_workspace_project_route_passthrough_without_workspace():
    """Without workspace, the project string passes through unchanged."""
    assert (
        _compose_workspace_project_route(
            workspace=None,
            project="my-project",
            project_id=None,
        )
        == "my-project"
    )


def test_write_note_workspace_project_route_combines_workspace_and_project():
    """workspace + project are joined as 'workspace/project'."""
    assert (
        _compose_workspace_project_route(
            workspace="acme",
            project="docs",
            project_id=None,
        )
        == "acme/docs"
    )


def test_write_note_workspace_project_route_passes_qualified_project_unchanged():
    """A pre-qualified 'workspace/project' string passes through when workspace is None."""
    assert (
        _compose_workspace_project_route(
            workspace=None,
            project="acme/docs",
            project_id=None,
        )
        == "acme/docs"
    )


@pytest.mark.parametrize(
    ("route_kwargs", "message"),
    [
        (
            {"workspace": " ", "project": "docs", "project_id": None},
            "workspace must not be empty",
        ),
        (
            {"workspace": "acme/extra", "project": "docs", "project_id": None},
            "workspace must be a single workspace",
        ),
        (
            {"workspace": "acme", "project": "docs", "project_id": "some-uuid"},
            "workspace cannot be combined with project_id",
        ),
        (
            {"workspace": "acme", "project": None, "project_id": None},
            "workspace requires an explicit project",
        ),
        (
            {"workspace": "acme", "project": "workspace/project", "project_id": None},
            "not both",
        ),
    ],
)
def test_write_note_workspace_project_route_rejects_invalid_inputs(route_kwargs, message):
    """Ambiguous workspace/project argument combinations should raise ValueError."""
    with pytest.raises(ValueError, match=message):
        _compose_workspace_project_route(**route_kwargs)


@pytest.mark.asyncio
async def test_write_note_accepts_workspace_param(app, test_project):
    """write_note routes correctly when workspace= is passed alongside project=."""
    # The test_project fixture gives us a project with a known name. Passing
    # workspace="" (blank) is invalid, so we test that the combined route is
    # built and that a valid workspace+project pair creates the note.
    result = await write_note(
        title="Workspace Routing Test",
        directory="ws-test",
        content="# Workspace Routing Test\n\nRouted via workspace param.",
        # project alone (no workspace) — confirms the parameter is accepted
        project=test_project.name,
    )
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result


@pytest.mark.asyncio
async def test_write_note_workspace_invalid_raises_before_routing(app, test_project):
    """Passing an empty workspace= should raise ValueError, not silently misbehave."""
    with pytest.raises(ValueError, match="workspace must not be empty"):
        await write_note(
            title="Should Fail",
            directory="ws-test",
            content="# Should Fail",
            workspace="",  # empty — must be rejected
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_write_note(app, test_project):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert f"permalink: {test_project.name}/test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Try reading it back via permalink
    content = await read_note("test/test-note", project=test_project.name)
    expected = (
        dedent("""
        ---
        title: Test Note
        type: note
        permalink: {permalink}
        tags:
        - test
        - documentation
        ---

        # Test
        This is a test note
        """)
        .format(permalink=f"{test_project.name}/test/test-note")
        .strip()
    )
    assert expected in _without_managed_timestamps(content)
    # write_note now stamps created/modified into frontmatter (#684)
    assert "created:" in content
    assert "modified:" in content


@pytest.mark.asyncio
async def test_write_note_no_tags(app, test_project):
    """Test creating a note without tags."""
    result = await write_note(
        project=test_project.name, title="Simple Note", directory="test", content="Just some text"
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Simple Note.md" in result
    assert f"permalink: {test_project.name}/test/simple-note" in result
    assert f"[Session: Using project '{test_project.name}']" in result
    # Should be able to read it back
    content = await read_note("test/simple-note", project=test_project.name)
    expected = (
        dedent("""
        ---
        title: Simple Note
        type: note
        permalink: {permalink}
        ---

        Just some text
        """)
        .format(permalink=f"{test_project.name}/test/simple-note")
        .strip()
    )
    assert expected in _without_managed_timestamps(content)


@pytest.mark.asyncio
async def test_write_note_update_existing(app, test_project):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result  # Got a valid permalink
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert f"permalink: {test_project.name}/test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    result = await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nThis is an updated note",
        tags=["test", "documentation"],
        overwrite=True,
    )
    assert "# Updated note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert f"permalink: {test_project.name}/test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Try reading it back
    content = await read_note("test/test-note", project=test_project.name)
    assert (
        dedent(
            """
        ---
        title: Test Note
        type: note
        permalink: {permalink}
        tags:
        - test
        - documentation
        ---

        # Test
        This is an updated note
        """
        )
        .format(permalink=f"{test_project.name}/test/test-note")
        .strip()
        == _without_managed_timestamps(content)
    )


@pytest.mark.asyncio
async def test_issue_93_write_note_respects_custom_permalink_new_note(app, test_project):
    """Test that write_note respects custom permalinks in frontmatter for new notes (Issue #93)"""

    # Create a note with custom permalink in frontmatter
    content_with_custom_permalink = dedent("""
        ---
        permalink: custom/my-desired-permalink
        ---

        # My New Note

        This note has a custom permalink specified in frontmatter.

        - [note] Testing if custom permalink is respected
    """).strip()

    result = await write_note(
        project=test_project.name,
        title="My New Note",
        directory="notes",
        content=content_with_custom_permalink,
    )

    # Verify the custom permalink is respected
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: notes/My New Note.md" in result
    assert "permalink: custom/my-desired-permalink" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_issue_93_write_note_respects_custom_permalink_existing_note(app, test_project):
    """Test that write_note respects custom permalinks when updating existing notes (Issue #93)"""

    # Step 1: Create initial note (auto-generated permalink)
    result1 = await write_note(
        project=test_project.name,
        title="Existing Note",
        directory="test",
        content="Initial content without custom permalink",
    )

    assert "# Created note" in result1
    assert f"project: {test_project.name}" in result1
    assert isinstance(result1, str)

    # Extract the auto-generated permalink
    initial_permalink = None
    for line in result1.split("\n"):
        if line.startswith("permalink:"):
            initial_permalink = line.split(":", 1)[1].strip()
            break

    assert initial_permalink is not None

    # Step 2: Update with content that includes custom permalink in frontmatter
    updated_content = dedent("""
        ---
        permalink: custom/new-permalink
        ---

        # Existing Note

        Updated content with custom permalink in frontmatter.

        - [note] Custom permalink should be respected on update
    """).strip()

    result2 = await write_note(
        project=test_project.name,
        title="Existing Note",
        directory="test",
        content=updated_content,
        overwrite=True,
    )

    # Verify the custom permalink is respected
    assert "# Updated note" in result2
    assert f"project: {test_project.name}" in result2
    assert "permalink: custom/new-permalink" in result2
    assert f"permalink: {initial_permalink}" not in result2
    assert f"[Session: Using project '{test_project.name}']" in result2


@pytest.mark.asyncio
async def test_delete_note_existing(app, test_project):
    """Test deleting a new note.

    Should:
    - Create entity with correct type and content
    - Return valid permalink
    - Delete the note
    """
    result = await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result
    assert f"project: {test_project.name}" in result

    deleted = await delete_note("test/test-note", project=test_project.name)
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_note_doesnt_exist(app, test_project):
    """Test deleting a new note.

    Should:
    - Delete the note
    - verify returns false
    """
    deleted = await delete_note("doesnt-exist", project=test_project.name)
    assert deleted is False


@pytest.mark.asyncio
async def test_write_note_with_tag_array_from_bug_report(app, test_project):
    """Test creating a note with a tag array as reported in issue #38.

    This reproduces the exact payload from the bug report where Cursor
    was passing an array of tags and getting a type mismatch error.
    """
    # This is the exact payload from the bug report
    bug_payload: dict[str, Any] = {
        "project": test_project.name,
        "title": "Title",
        "directory": "folder",
        "content": "CONTENT",
        "tags": ["hipporag", "search", "fallback", "symfony", "error-handling"],
    }

    # Try to call the function with this data directly
    result = await write_note(**bug_payload)

    assert result
    assert f"project: {test_project.name}" in result
    assert f"permalink: {test_project.name}/folder/title" in result
    assert "Tags" in result
    assert "hipporag" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_write_note_verbose(app, test_project, engine_factory):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="""
# Test\nThis is a test note

- [note] First observation
- "relates to" [[Knowledge]]

""",
        tags=["test", "documentation"],
    )

    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert f"permalink: {test_project.name}/test/test-note" in result
    assert "## Observations" in result
    assert "- note: 1" in result
    assert "## Relations" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    _, session_maker = engine_factory
    relation_repository = RelationRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_type(session, "relates to")
    assert any(relation.to_name == "Knowledge" for relation in relations)


@pytest.mark.asyncio
async def test_write_note_preserves_custom_metadata(app, project_config, test_project):
    """Test that updating a note preserves custom metadata fields.

    Reproduces issue #36 where custom frontmatter fields like Status
    were being lost when updating notes with the write_note tool.

    Should:
    - Create a note with custom frontmatter
    - Update the note with new content
    - Verify custom frontmatter is preserved
    """
    # First, create a note with custom metadata using write_note
    await write_note(
        project=test_project.name,
        title="Custom Metadata Note",
        directory="test",
        content="# Initial content",
        tags=["test"],
    )

    # Read the note to get its permalink
    content = await read_note("test/custom-metadata-note", project=test_project.name)

    # Now directly update the file with custom frontmatter
    # We need to use a direct file update to add custom frontmatter
    import frontmatter

    file_path = project_config.home / "test" / "Custom Metadata Note.md"
    post = frontmatter.load(file_path)

    # Add custom frontmatter
    post["Status"] = "In Progress"
    post["Priority"] = "High"
    post["Version"] = "1.0"

    # Write the file back
    with open(file_path, "w") as f:
        f.write(frontmatter.dumps(post))

    # Now update the note using write_note
    result = await write_note(
        project=test_project.name,
        title="Custom Metadata Note",
        directory="test",
        content="# Updated content",
        tags=["test", "updated"],
        overwrite=True,
    )

    # Verify the update was successful
    assert (
        "Updated note\nproject: test-project\nfile_path: test/Custom Metadata Note.md"
    ) in result
    assert f"project: {test_project.name}" in result

    # Read the note back and check if custom frontmatter is preserved
    content = await read_note("test/custom-metadata-note", project=test_project.name)

    # Custom frontmatter should be preserved
    assert "Status: In Progress" in content
    assert "Priority: High" in content
    # Version might be quoted as '1.0' due to YAML serialization
    assert "Version:" in content  # Just check that the field exists
    assert "1.0" in content  # And that the value exists somewhere

    # And new content should be there
    assert "# Updated content" in content

    # And tags should be updated (without # prefix)
    assert "- test" in content
    assert "- updated" in content


@pytest.mark.asyncio
async def test_write_note_preserves_content_frontmatter(app, test_project):
    """Test creating a new note."""
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content=dedent(
            """
            ---
            title: Test Note
            type: note
            version: 1.0
            author: name
            ---
            # Test

            This is a test note
            """
        ),
        tags=["test", "documentation"],
    )

    # Try reading it back via permalink
    content = await read_note("test/test-note", project=test_project.name)
    assert (
        dedent(
            """
            ---
            title: Test Note
            type: note
            permalink: {permalink}
            version: 1.0
            author: name
            tags:
            - test
            - documentation
            ---

            # Test

            This is a test note
            """
        )
        .format(permalink=f"{test_project.name}/test/test-note")
        .strip()
        in _without_managed_timestamps(content)
    )


@pytest.mark.asyncio
async def test_write_note_single_line_inline_fence_is_body_issue_972(app, test_project):
    """Single-line content starting with `---` must be stored as body, not frontmatter.

    Reproduces issue #972: a one-line string where `\\n` are literal backslash-n
    characters (a common CLI/agent input shape) was misread as frontmatter, merging a
    garbage `\\nstatus` YAML key into the note and silently dropping the inline
    `---...---` segment from the body.
    """
    one_line = r"---\nstatus: active\n---\nDiscussed Q3 roadmap with Anthony."

    await write_note(
        project=test_project.name,
        title="Meeting Notes",
        directory="meetings",
        content=one_line,
    )

    content = await read_note("meetings/meeting-notes", project=test_project.name)
    assert isinstance(content, str)

    # The literal one-line string survives verbatim in the body...
    assert one_line in content

    # ...and no garbage `\nstatus` key leaked into the generated YAML frontmatter.
    # Inspect only the frontmatter block (between the first pair of fence lines).
    lines = content.splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    frontmatter_block = lines[1:closing]
    assert not any("status" in line for line in frontmatter_block)


@pytest.mark.asyncio
async def test_write_note_permalink_collision_fix_issue_139(app, test_project):
    """Test fix for GitHub Issue #139: UNIQUE constraint failed: entity.permalink.

    This reproduces the exact scenario described in the issue:
    1. Create a note with title "Note 1"
    2. Create another note with title "Note 2"
    3. Try to create/replace first note again with same title "Note 1"

    Before the fix, step 3 would fail with UNIQUE constraint error.
    After the fix, it should either update the existing note or create with unique permalink.
    """
    # Step 1: Create first note
    result1 = await write_note(
        project=test_project.name,
        title="Note 1",
        directory="test",
        content="Original content for note 1",
    )
    assert "# Created note" in result1
    assert f"project: {test_project.name}" in result1
    assert f"permalink: {test_project.name}/test/note-1" in result1

    # Step 2: Create second note with different title
    result2 = await write_note(
        project=test_project.name, title="Note 2", directory="test", content="Content for note 2"
    )
    assert "# Created note" in result2
    assert f"project: {test_project.name}" in result2
    assert f"permalink: {test_project.name}/test/note-2" in result2

    # Step 3: Try to create/replace first note again
    # This scenario would trigger the UNIQUE constraint failure before the fix
    result3 = await write_note(
        project=test_project.name,
        title="Note 1",  # Same title as first note
        directory="test",  # Same folder as first note
        content="Replacement content for note 1",  # Different content
        overwrite=True,
    )

    # This should not raise a UNIQUE constraint failure error
    # It should succeed and either:
    # 1. Update the existing note (preferred behavior)
    # 2. Create a new note with unique permalink (fallback behavior)

    assert result3 is not None
    assert f"project: {test_project.name}" in result3
    assert "Updated note" in result3 or "Created note" in result3

    # The result should contain either the original permalink or a unique one
    assert (
        f"permalink: {test_project.name}/test/note-1" in result3
        or f"permalink: {test_project.name}/test/note-1-1" in result3
    )

    # Verify we can read back the content
    if f"permalink: {test_project.name}/test/note-1" in result3:
        # Updated existing note case
        content = await read_note("test/note-1", project=test_project.name)
        assert "Replacement content for note 1" in content
    else:
        # Created new note with unique permalink case
        content = await read_note(test_project.name, "test/note-1-1")
        assert "Replacement content for note 1" in content
        # Original note should still exist
        original_content = await read_note(test_project.name, "test/note-1")
        assert "Original content for note 1" in original_content


@pytest.mark.asyncio
async def test_write_note_with_custom_note_type(app, test_project):
    """Test creating a note with custom note_type parameter.

    This test verifies the fix for Issue #144 where note_type parameter
    was hardcoded to "note" instead of allowing custom types.
    """
    result = await write_note(
        project=test_project.name,
        title="Test Guide",
        directory="guides",
        content="# Guide Content\nThis is a guide",
        tags=["guide", "documentation"],
        note_type="guide",
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: guides/Test Guide.md" in result
    assert f"permalink: {test_project.name}/guides/test-guide" in result
    assert "## Tags" in result
    assert "- guide, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the note type is correctly set in the frontmatter
    content = await read_note("guides/test-guide", project=test_project.name)
    expected = (
        dedent("""
        ---
        title: Test Guide
        type: guide
        permalink: {permalink}
        tags:
        - guide
        - documentation
        ---

        # Guide Content
        This is a guide
        """)
        .format(permalink=f"{test_project.name}/guides/test-guide")
        .strip()
    )
    assert expected in _without_managed_timestamps(content)


@pytest.mark.asyncio
async def test_write_note_with_report_note_type(app, test_project):
    """Test creating a note with note_type="report"."""
    result = await write_note(
        project=test_project.name,
        title="Monthly Report",
        directory="reports",
        content="# Monthly Report\nThis is a monthly report",
        tags=["report", "monthly"],
        note_type="report",
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: reports/Monthly Report.md" in result
    assert f"permalink: {test_project.name}/reports/monthly-report" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the note type is correctly set in the frontmatter
    content = await read_note("reports/monthly-report", project=test_project.name)
    assert "type: report" in content
    assert "# Monthly Report" in content


@pytest.mark.asyncio
async def test_write_note_with_config_note_type(app, test_project):
    """Test creating a note with note_type="config"."""
    result = await write_note(
        project=test_project.name,
        title="System Config",
        directory="config",
        content="# System Configuration\nThis is a config file",
        note_type="config",
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: config/System Config.md" in result
    assert f"permalink: {test_project.name}/config/system-config" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the note type is correctly set in the frontmatter
    content = await read_note("config/system-config", project=test_project.name)
    assert "type: config" in content
    assert "# System Configuration" in content


@pytest.mark.asyncio
async def test_write_note_note_type_default_behavior(app, test_project):
    """Test that the note_type parameter defaults to "note" when not specified.

    This ensures backward compatibility - existing code that doesn't specify
    note_type should continue to work as before.
    """
    result = await write_note(
        project=test_project.name,
        title="Default Type Test",
        directory="test",
        content="# Default Type Test\nThis should be type 'note'",
        tags=["test"],
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Default Type Test.md" in result
    assert f"permalink: {test_project.name}/test/default-type-test" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the note type defaults to "note"
    content = await read_note("test/default-type-test", project=test_project.name)
    assert "type: note" in content
    assert "# Default Type Test" in content


@pytest.mark.asyncio
async def test_write_note_update_existing_with_different_note_type(app, test_project):
    """Test updating an existing note with a different note_type."""
    # Create initial note as "note" type
    result1 = await write_note(
        project=test_project.name,
        title="Changeable Type",
        directory="test",
        content="# Initial Content\nThis starts as a note",
        tags=["test"],
        note_type="note",
    )

    assert result1
    assert "# Created note" in result1
    assert f"project: {test_project.name}" in result1

    # Update the same note with a different note_type
    result2 = await write_note(
        project=test_project.name,
        title="Changeable Type",
        directory="test",
        content="# Updated Content\nThis is now a guide",
        tags=["guide"],
        note_type="guide",
        overwrite=True,
    )

    assert result2
    assert "# Updated note" in result2
    assert f"project: {test_project.name}" in result2

    # Verify the note type was updated
    content = await read_note("test/changeable-type", project=test_project.name)
    assert "type: guide" in content
    assert "# Updated Content" in content
    assert "- guide" in content


@pytest.mark.asyncio
async def test_write_note_respects_frontmatter_note_type(app, test_project):
    """Test that note_type in frontmatter is respected when parameter is not provided.

    This verifies that when write_note is called without note_type parameter,
    but the content includes frontmatter with a 'type' field, that type is respected
    instead of defaulting to 'note'.
    """
    note = dedent("""
        ---
        title: Test Guide
        type: guide
        permalink: guides/test-guide
        tags:
        - guide
        - documentation
        ---

        # Guide Content
        This is a guide
        """).strip()

    # Call write_note without note_type parameter - it should respect frontmatter type
    result = await write_note(
        project=test_project.name, title="Test Guide", directory="guides", content=note
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: guides/Test Guide.md" in result
    assert "permalink: guides/test-guide" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the note type from frontmatter is respected (should be "guide", not "note")
    content = await read_note("guides/test-guide", project=test_project.name)
    assert "type: guide" in content
    assert "# Guide Content" in content
    assert "- guide" in content
    assert "- documentation" in content


class TestWriteNoteSecurityValidation:
    """Test write_note security validation features."""

    @pytest.mark.asyncio
    async def test_write_note_blocks_path_traversal_unix(self, app, test_project):
        """Test that Unix-style path traversal attacks are blocked in folder parameter."""
        # Test various Unix-style path traversal patterns
        attack_folders = [
            "../",
            "../../",
            "../../../",
            "../secrets",
            "../../etc",
            "../../../etc/passwd_folder",
            "notes/../../../etc",
            "folder/../../outside",
            "../../../../malicious",
        ]

        for attack_folder in attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Test Note",
                directory=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_path_traversal_windows(self, app, test_project):
        """Test that Windows-style path traversal attacks are blocked in folder parameter."""
        # Test various Windows-style path traversal patterns
        attack_folders = [
            "..\\",
            "..\\..\\",
            "..\\..\\..\\",
            "..\\secrets",
            "..\\..\\Windows",
            "..\\..\\..\\Windows\\System32",
            "notes\\..\\..\\..\\Windows",
            "\\\\server\\share",
            "\\\\..\\..\\Windows",
        ]

        for attack_folder in attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Test Note",
                directory=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_absolute_paths(self, app, test_project):
        """Test that absolute paths are blocked in folder parameter."""
        # Test various absolute path patterns
        attack_folders = [
            "/etc",
            "/home/user",
            "/var/log",
            "/root",
            "C:\\Windows",
            "C:\\Users\\user",
            "D:\\secrets",
            "/tmp/malicious",
            "/usr/local/evil",
        ]

        for attack_folder in attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Test Note",
                directory=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_home_directory_access(self, app, test_project):
        """Test that home directory access patterns are blocked in folder parameter."""
        # Test various home directory access patterns
        attack_folders = [
            "~",
            "~/",
            "~/secrets",
            "~/.ssh",
            "~/Documents",
            "~\\AppData",
            "~\\Desktop",
            "~/.env_folder",
        ]

        for attack_folder in attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Test Note",
                directory=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_mixed_attack_patterns(self, app, test_project):
        """Test that mixed legitimate/attack patterns are blocked in folder parameter."""
        # Test mixed patterns that start legitimate but contain attacks
        attack_folders = [
            "notes/../../../etc",
            "docs/../../.env_folder",
            "legitimate/path/../../.ssh",
            "project/folder/../../../Windows",
            "valid/folder/../../home/user",
            "assets/../../../tmp/evil",
        ]

        for attack_folder in attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Test Note",
                directory=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_allows_safe_folder_paths(self, app, test_project):
        """Test that legitimate folder paths are still allowed."""
        # Test various safe folder patterns
        safe_folders = [
            "notes",
            "docs",
            "projects/2025",
            "archive/old-notes",
            "deep/nested/directory/structure",
            "folder/subfolder",
            "research/ml",
            "meeting-notes",
        ]

        for safe_folder in safe_folders:
            result = await write_note(
                project=test_project.name,
                title=f"Test Note in {safe_folder.replace('/', '-')}",
                directory=safe_folder,
                content="# Test Content\nThis should work normally with security validation.",
                tags=["test", "security"],
            )

            # Should succeed (not a security error)
            assert isinstance(result, str)
            assert "# Error" not in result
            assert "paths must stay within project boundaries" not in result
            # Should be normal successful creation/update
            assert ("# Created note" in result) or ("# Updated note" in result)
            assert safe_folder in result  # Should show in file_path

    @pytest.mark.asyncio
    async def test_write_note_empty_folder_security(self, app, test_project):
        """Test that empty folder parameter is handled securely."""
        # Empty folder should be allowed (creates in root)
        result = await write_note(
            project=test_project.name,
            title="Root Note",
            directory="",
            content="# Root Note\nThis note should be created in the project root.",
        )

        assert isinstance(result, str)
        # Empty folder should not trigger security error
        assert "# Error" not in result
        assert "paths must stay within project boundaries" not in result
        # Should succeed normally
        assert ("# Created note" in result) or ("# Updated note" in result)

    @pytest.mark.asyncio
    async def test_write_note_none_folder_security(self, app, test_project):
        """Test that default folder behavior works securely when folder is omitted."""
        # The write_note function requires folder parameter, but we can test with empty string
        # which effectively creates in project root
        result = await write_note(
            project=test_project.name,
            title="Root Folder Note",
            directory="",  # Empty string instead of None since folder is required
            content="# Root Folder Note\nThis note should be created in the project root.",
        )

        assert isinstance(result, str)
        # Empty folder should not trigger security error
        assert "# Error" not in result
        assert "paths must stay within project boundaries" not in result
        # Should succeed normally
        assert ("# Created note" in result) or ("# Updated note" in result)

    @pytest.mark.asyncio
    async def test_write_note_current_directory_references_security(self, app, test_project):
        """Test that current directory references are handled securely."""
        # Test current directory references (should be safe)
        safe_folders = [
            "./notes",
            "folder/./subfolder",
            "./folder/subfolder",
        ]

        for safe_folder in safe_folders:
            result = await write_note(
                project=test_project.name,
                title=f"Current Dir Test {safe_folder.replace('/', '-').replace('.', 'dot')}",
                directory=safe_folder,
                content="# Current Directory Test\nThis should work with current directory references.",
            )

            assert isinstance(result, str)
            # Should NOT contain security error message
            assert "# Error" not in result
            assert "paths must stay within project boundaries" not in result
            # Should succeed normally
            assert ("# Created note" in result) or ("# Updated note" in result)

    @pytest.mark.asyncio
    async def test_write_note_security_with_all_parameters(self, app, test_project):
        """Test security validation works with all write_note parameters."""
        # Test that security validation is applied even when all other parameters are provided
        result = await write_note(
            project=test_project.name,
            title="Security Test with All Params",
            directory="../../../etc/malicious",
            content="# Malicious Content\nThis should be blocked by security validation.",
            tags=["malicious", "test"],
            note_type="guide",
        )

        assert isinstance(result, str)
        assert "# Error" in result
        assert "paths must stay within project boundaries" in result
        assert "../../../etc/malicious" in result

    @pytest.mark.asyncio
    async def test_write_note_security_logging(self, app, test_project, caplog):
        """Test that security violations are properly logged."""
        # Attempt path traversal attack
        result = await write_note(
            project=test_project.name,
            title="Security Logging Test",
            directory="../../../etc/passwd_folder",
            content="# Test Content\nThis should trigger security logging.",
        )

        assert "# Error" in result
        assert "paths must stay within project boundaries" in result

        # Check that security violation was logged
        # Note: This test may need adjustment based on the actual logging setup
        # The security validation should generate a warning log entry

    @pytest.mark.asyncio
    async def test_write_note_preserves_functionality_with_security(self, app, test_project):
        """Test that security validation doesn't break normal note creation functionality."""
        # Create a note with all features to ensure security validation doesn't interfere
        result = await write_note(
            project=test_project.name,
            title="Full Feature Security Test",
            directory="security-tests",
            content=dedent("""
                # Full Feature Security Test

                This note tests that security validation doesn't break normal functionality.

                ## Observations
                - [security] Path validation working correctly #security
                - [feature] All features still functional #test

                ## Relations
                - relates_to [[Security Implementation]]
                - depends_on [[Path Validation]]

                Additional content with various formatting.
            """).strip(),
            tags=["security", "test", "full-feature"],
            note_type="guide",
        )

        # Should succeed normally
        assert isinstance(result, str)
        assert "# Error" not in result
        assert "paths must stay within project boundaries" not in result
        assert "# Created note" in result
        assert "file_path: security-tests/Full Feature Security Test.md" in result
        assert f"permalink: {test_project.name}/security-tests/full-feature-security-test" in result

        # Should process observations and relations
        assert "## Observations" in result
        assert "## Relations" in result
        assert "## Tags" in result

        # Should show proper counts
        assert "security: 1" in result
        assert "feature: 1" in result


class TestWriteNoteSecurityEdgeCases:
    """Test edge cases for write_note security validation."""

    @pytest.mark.asyncio
    async def test_write_note_unicode_folder_attacks(self, app, test_project):
        """Test that Unicode-based path traversal attempts are blocked."""
        # Test Unicode path traversal attempts
        unicode_attack_folders = [
            "notes/文档/../../../etc",  # Chinese characters
            "docs/café/../../secrets",  # Accented characters
            "files/αβγ/../../../malicious",  # Greek characters
        ]

        for attack_folder in unicode_attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Unicode Attack Test",
                directory=attack_folder,
                content="# Unicode Attack\nThis should be blocked.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_very_long_attack_folder(self, app, test_project):
        """Test handling of very long attack folder paths."""
        # Create a very long path traversal attack
        long_attack_folder = "../" * 1000 + "etc/malicious"

        result = await write_note(
            project=test_project.name,
            title="Long Attack Test",
            directory=long_attack_folder,
            content="# Long Attack\nThis should be blocked.",
        )

        assert isinstance(result, str)
        assert "# Error" in result
        assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_case_variations_attacks(self, app, test_project):
        """Test that case variations don't bypass security."""
        # Test case variations (though case sensitivity depends on filesystem)
        case_attack_folders = [
            "../ETC",
            "../Etc/SECRETS",
            "..\\WINDOWS",
            "~/SECRETS",
        ]

        for attack_folder in case_attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Case Variation Attack Test",
                directory=attack_folder,
                content="# Case Attack\nThis should be blocked.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_whitespace_in_attack_folders(self, app, test_project):
        """Test that whitespace doesn't help bypass security."""
        # Test attack folders with various whitespace
        whitespace_attack_folders = [
            " ../../../etc ",
            "\t../../../secrets\t",
            " ..\\..\\Windows ",
            "notes/ ../../ malicious",
        ]

        for attack_folder in whitespace_attack_folders:
            result = await write_note(
                project=test_project.name,
                title="Whitespace Attack Test",
                directory=attack_folder,
                content="# Whitespace Attack\nThis should be blocked.",
            )

            assert isinstance(result, str)
            # The attack should still be blocked even with whitespace
            if ".." in attack_folder.strip() or "~" in attack_folder.strip():
                assert "# Error" in result
                assert "paths must stay within project boundaries" in result


class TestWriteNoteOverwriteGuard:
    """Test the write_note overwrite guard feature (Issue #625)."""

    @pytest.mark.asyncio
    async def test_write_note_blocks_overwrite_by_default(self, app, test_project):
        """Second write_note to same title/directory returns error, original content untouched."""
        # Create initial note
        result1 = await write_note(
            project=test_project.name,
            title="Guard Test",
            directory="guard",
            content="# Guard Test\n\nOriginal content",
        )
        assert "# Created note" in result1

        # Second write without overwrite should be blocked
        result2 = await write_note(
            project=test_project.name,
            title="Guard Test",
            directory="guard",
            content="# Guard Test\n\nReplacement content",
        )
        assert "# Error: Note already exists" in result2
        assert "Guard Test" in result2
        assert "edit_note" in result2

        # Original content should be untouched
        content = await read_note("guard/guard-test", project=test_project.name)
        assert "Original content" in content
        assert "Replacement content" not in content

    @pytest.mark.asyncio
    async def test_write_note_overwrite_false_explicit(self, app, test_project):
        """Explicit overwrite=False behaves same as default."""
        await write_note(
            project=test_project.name,
            title="Explicit False",
            directory="guard",
            content="# Explicit False\n\nOriginal",
        )

        result = await write_note(
            project=test_project.name,
            title="Explicit False",
            directory="guard",
            content="# Explicit False\n\nReplacement",
            overwrite=False,
        )
        assert "# Error: Note already exists" in result

    @pytest.mark.asyncio
    async def test_write_note_overwrite_true_replaces(self, app, test_project):
        """Explicit overwrite=True performs the update."""
        await write_note(
            project=test_project.name,
            title="Overwrite True",
            directory="guard",
            content="# Overwrite True\n\nOriginal content",
        )

        result = await write_note(
            project=test_project.name,
            title="Overwrite True",
            directory="guard",
            content="# Overwrite True\n\nReplacement content",
            overwrite=True,
        )
        assert "# Updated note" in result

        # Verify content was replaced
        content = await read_note("guard/overwrite-true", project=test_project.name)
        assert "Replacement content" in content
        assert "Original content" not in content

    @pytest.mark.asyncio
    async def test_write_note_overwrite_error_json_format(self, app, test_project):
        """JSON output returns structured error with NOTE_ALREADY_EXISTS."""
        await write_note(
            project=test_project.name,
            title="JSON Guard",
            directory="guard",
            content="# JSON Guard\n\nOriginal",
        )

        result = await write_note(
            project=test_project.name,
            title="JSON Guard",
            directory="guard",
            content="# JSON Guard\n\nReplacement",
            output_format="json",
        )
        assert isinstance(result, dict)
        assert result["error"] == "NOTE_ALREADY_EXISTS"
        assert result["action"] == "conflict"
        assert result["title"] == "JSON Guard"
        assert result["permalink"] is not None

    @pytest.mark.asyncio
    async def test_write_note_config_overwrite_default_true(
        self, app, test_project, app_config, config_manager
    ):
        """Config write_note_overwrite_default=True restores old upsert behavior."""
        # Set config to allow overwrites by default
        app_config.write_note_overwrite_default = True
        config_module._CONFIG_CACHE = app_config
        # Pin mtime+size to the on-disk file so the cache guard sees a match
        # and keeps our injected config instead of re-reading from disk.
        _st = config_manager.config_file.stat()
        config_module._CONFIG_MTIME = _st.st_mtime
        config_module._CONFIG_SIZE = _st.st_size

        try:
            await write_note(
                project=test_project.name,
                title="Config Default",
                directory="guard",
                content="# Config Default\n\nOriginal",
            )

            result = await write_note(
                project=test_project.name,
                title="Config Default",
                directory="guard",
                content="# Config Default\n\nReplacement via config default",
            )
            # Should succeed as update because config default is True
            assert "# Updated note" in result

            content = await read_note("guard/config-default", project=test_project.name)
            assert "Replacement via config default" in content
        finally:
            # Restore config
            app_config.write_note_overwrite_default = False
            config_module._CONFIG_CACHE = app_config
            _st = config_manager.config_file.stat()
            config_module._CONFIG_MTIME = _st.st_mtime
            config_module._CONFIG_SIZE = _st.st_size

    @pytest.mark.asyncio
    async def test_write_note_new_note_unaffected(self, app, test_project):
        """Guard only triggers on conflict — new notes are created normally."""
        result = await write_note(
            project=test_project.name,
            title="Brand New Note",
            directory="guard",
            content="# Brand New Note\n\nFresh content",
            tags=["new"],
        )
        assert "# Created note" in result
        assert f"project: {test_project.name}" in result
        assert "file_path: guard/Brand New Note.md" in result

    @pytest.mark.asyncio
    async def test_write_note_overwrite_resolves_by_file_path_strictly(
        self, app, test_project, entity_repository, session_maker, monkeypatch
    ):
        """Regression: overwrite=True must resolve the conflicting entity by
        file_path with strict=True, not by permalink with fuzzy fallback.

        Bug shape: in workspace-prefixed palaces the client-built permalink
        omits the workspace slug, so resolve_entity(permalink) with the default
        strict=False would fall through to fuzzy search and could pick an
        orphan row sharing tokens with the canonical permalink. The update
        then wrote to the orphan, the canonical row stayed stale, and the
        next overwrite minted a -1/-2 suffix because the permalink uniqueness
        check found duplicate rows.

        The 409 we catch came from a file_service.exists(file_path) check,
        so file_path is the authoritative key — strict resolution against it
        is safe even when permalinks are workspace-prefixed elsewhere.
        """
        # Spy on the resolve_entity call to assert the identifier and strict flag.
        from basic_memory.mcp.clients import knowledge as knowledge_mod

        original_resolve = knowledge_mod.KnowledgeClient.resolve_entity
        captured: dict[str, Any] = {}

        async def spy_resolve(self, identifier, *, strict=False):
            captured["identifier"] = identifier
            captured["strict"] = strict
            return await original_resolve(self, identifier, strict=strict)

        monkeypatch.setattr(knowledge_mod.KnowledgeClient, "resolve_entity", spy_resolve)

        # Create then overwrite the canonical note.
        await write_note(
            project=test_project.name,
            title="Overview",
            directory="features/foo",
            content="# Overview\n\nVersion A",
        )
        canonical_permalink = f"{test_project.name}/features/foo/overview"
        async with db.scoped_session(session_maker) as session:
            canonical = await entity_repository.get_by_permalink(session, canonical_permalink)
        assert canonical is not None
        canonical_id = canonical.id

        result = await write_note(
            project=test_project.name,
            title="Overview",
            directory="features/foo",
            content="# Overview\n\nVersion B",
            overwrite=True,
        )
        assert "# Updated note" in result

        # The overwrite path resolved by file_path with strict=True — not by
        # permalink with the default fuzzy fallback.
        assert captured.get("identifier") == "features/foo/Overview.md"
        assert captured.get("strict") is True

        # And the canonical row was updated in place — no duplicate -1/-2 row.
        async with db.scoped_session(session_maker) as session:
            canonical_after = await entity_repository.get_by_permalink(session, canonical_permalink)
        assert canonical_after is not None
        assert canonical_after.id == canonical_id

        content = await read_note(canonical_permalink, project=test_project.name)
        assert "Version B" in content
        assert "Version A" not in content

        for suffix in ("-1", "-2"):
            async with db.scoped_session(session_maker) as session:
                stray = await entity_repository.get_by_permalink(
                    session, f"{canonical_permalink}{suffix}"
                )
            assert stray is None, (
                f"overwrite=True minted a stray '{suffix}' suffix on the canonical permalink"
            )
