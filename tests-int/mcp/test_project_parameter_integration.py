"""Integration tests for project parameter functionality in MCP tools.

These tests verify that the project parameter actually works with real projects
and data, not just mocks.
"""

import pytest
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.mcp.tools.search import search_notes
from basic_memory.mcp.tools.delete_note import delete_note
from basic_memory.mcp.tools.build_context import build_context
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.read_content import read_content
from basic_memory.mcp.tools.canvas import canvas
from basic_memory.mcp.project_session import session
from basic_memory.repository.project_repository import ProjectRepository


@pytest.mark.asyncio
async def test_write_note_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that write_note can write to a specific project."""
    # Set current project to first project
    session.set_current_project(test_project.permalink)

    # Write a note to the second project (override current)
    result = await write_note(
        title="Project Specific Note",
        content="This note was written to the second project",
        folder="test",
        project=second_project.permalink,
    )

    # Verify the note was created
    assert "Created note" in result
    assert "project-specific-note" in result

    # Verify we can read it back from the second project
    read_result = await read_note("Project Specific Note", project=second_project.permalink)
    assert "This note was written to the second project" in read_result


@pytest.mark.asyncio
async def test_read_note_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that read_note can read from a specific project."""

    # Write notes to both projects with the same title
    await write_note(
        title="Same Title Note",
        content="Content from first project",
        folder="test",
        project=test_project.permalink,
    )

    await write_note(
        title="Same Title Note",
        content="Content from second project",
        folder="test",
        project=second_project.permalink,
    )

    # Read from first project
    first_result = await read_note("Same Title Note", project=test_project.permalink)
    assert "Content from first project" in first_result

    # Read from second project
    second_result = await read_note("Same Title Note", project=second_project.permalink)
    assert "Content from second project" in second_result

    # Verify they are different
    assert first_result != second_result


@pytest.mark.asyncio
async def test_search_notes_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that search_notes can search within a specific project."""
    # Write unique notes to each project
    await write_note(
        title="First Project Note",
        content="This contains unique keyword apple",
        folder="test",
        project=test_project.permalink,
    )

    await write_note(
        title="Second Project Note",
        content="This contains unique keyword banana",
        folder="test",
        project=second_project.permalink,
    )

    # Search in first project - should find apple but not banana
    first_results = await search_notes("apple", project=test_project.permalink)
    assert len(first_results.results) >= 1
    assert any("apple" in result.content for result in first_results.results if result.content)

    # Search in second project - should find banana but not apple
    second_results = await search_notes("banana", project=second_project.permalink)
    assert len(second_results.results) >= 1
    assert any("banana" in result.content for result in second_results.results if result.content)

    # Cross-verify: search for apple in second project should find nothing
    cross_results = await search_notes("apple", project=second_project.permalink)
    assert len(cross_results.results) == 0


@pytest.mark.asyncio
async def test_delete_note_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that delete_note can delete from a specific project."""

    # Write notes with same title to both projects
    await write_note(
        title="Delete Target Note",
        content="Note in first project",
        folder="test",
        project=test_project.permalink,
    )

    await write_note(
        title="Delete Target Note",
        content="Note in second project",
        folder="test",
        project=second_project.permalink,
    )

    # Verify both notes exist
    first_note = await read_note("Delete Target Note", project=test_project.permalink)
    assert "Note in first project" in first_note

    second_note = await read_note("Delete Target Note", project=second_project.permalink)
    assert "Note in second project" in second_note

    # Delete from second project only
    delete_result = await delete_note("Delete Target Note", project=second_project.permalink)
    assert delete_result is True

    # Verify first project note still exists
    first_note_after = await read_note("Delete Target Note", project=test_project.permalink)
    assert "Note in first project" in first_note_after

    # Verify second project note is gone (should return not found message)
    second_note_after = await read_note("Delete Target Note", project=second_project.permalink)
    assert "Note Not Found" in second_note_after


@pytest.mark.asyncio
async def test_project_isolation(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that projects are properly isolated from each other."""

    # Write notes to each project
    await write_note(
        title="Isolation Test Note",
        content="Content from project A with tag #projecta",
        folder="test",
        project=test_project.permalink,
    )

    await write_note(
        title="Isolation Test Note",
        content="Content from project B with tag #projectb",
        folder="test",
        project=second_project.permalink,
    )

    await write_note(
        title="Another Note",
        content="More content in project A",
        folder="test",
        project=test_project.permalink,
    )

    # Test search isolation
    a_results = await search_notes("projecta", project=test_project.permalink)
    b_results = await search_notes("projectb", project=second_project.permalink)

    # Each project should only find its own content
    assert len(a_results.results) >= 1
    assert len(b_results.results) >= 1

    # Cross-search should find nothing
    a_cross_results = await search_notes("projectb", project=test_project.permalink)
    b_cross_results = await search_notes("projecta", project=second_project.permalink)

    assert len(a_cross_results.results) == 0
    assert len(b_cross_results.results) == 0

    # Test read isolation
    a_note = await read_note("Isolation Test Note", project=test_project.permalink)
    b_note = await read_note("Isolation Test Note", project=second_project.permalink)

    assert "#projecta" in a_note
    assert "#projectb" in b_note
    assert "#projecta" not in b_note
    assert "#projectb" not in a_note


@pytest.mark.asyncio
async def test_current_project_fallback(multi_project_app, client):
    """Test that tools fall back to current project when no project parameter given."""
    # Set current project
    session.set_current_project("test-project")

    # Write a note without project parameter (should use current)
    result = await write_note(
        title="Current Project Note",
        content="This should go to the current project",
        folder="test",
        # No project parameter
    )

    assert "Created note" in result

    # Read it back without project parameter (should use current)
    read_result = await read_note("Current Project Note")
    assert "This should go to the current project" in read_result

    # Search without project parameter (should use current)
    search_results = await search_notes("current project")
    assert len(search_results.results) >= 1
    assert any(
        "current project" in result.content.lower()
        for result in search_results.results
        if result.content
    )


@pytest.mark.asyncio
async def test_project_parameter_overrides_current(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that project parameter overrides the current project setting."""
    # Set current project to test-project
    session.set_current_project(test_project.permalink)

    # Write to override project (should ignore current project)
    result = await write_note(
        title="Override Test Note",
        content="This goes to override project despite current setting",
        folder="test",
        project=second_project.permalink,
    )

    assert "Created note" in result

    # Try to read from current project - should not find it
    current_result = await read_note("Override Test Note", project=test_project.permalink)
    assert "Note Not Found" in current_result

    # Read from override project - should find it
    override_result = await read_note("Override Test Note", project=second_project.permalink)
    assert "This goes to override project" in override_result


@pytest.mark.asyncio
async def test_read_content_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that read_content can read from a specific project."""
    # Write a file to the second project
    await write_note(
        title="Content Test File",
        content="Raw file content for testing",
        folder="files",
        project=second_project.permalink,
    )

    # Read the raw content from the second project
    content_result = await read_content(
        "files/Content Test File.md", project=second_project.permalink
    )
    # read_content returns a dict with the content in the 'text' field
    assert "Raw file content for testing" in str(content_result)


@pytest.mark.asyncio
async def test_canvas_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that canvas can create in a specific project."""
    # Create canvas in second project
    nodes = [
        {
            "id": "1",
            "type": "text",
            "text": "Test Node",
            "x": 100,
            "y": 100,
            "width": 200,
            "height": 100,
        }
    ]
    edges = []

    result = await canvas(
        nodes=nodes,
        edges=edges,
        title="Test Canvas",
        folder="diagrams",
        project=second_project.permalink,
    )

    # canvas returns a success message
    assert "canvas" in result.lower() or "created" in result.lower()


@pytest.mark.asyncio
async def test_recent_activity_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that recent_activity can query a specific project."""
    # Write notes to both projects
    await write_note(
        title="Recent Activity Test 1",
        content="Content in first project",
        folder="recent",
        project=test_project.permalink,
    )

    await write_note(
        title="Recent Activity Test 2",
        content="Content in second project",
        folder="recent",
        project=second_project.permalink,
    )

    # Get recent activity from second project only
    recent_results = await recent_activity(project=second_project.permalink)

    # Should contain activity from second project
    assert "Recent Activity Test 2" in str(recent_results) or "second project" in str(
        recent_results
    )


@pytest.mark.asyncio
async def test_build_context_with_project_parameter(
    multi_project_app, test_project, second_project, project_repository: ProjectRepository
):
    """Test that build_context can build from a specific project."""
    # Write related notes to second project
    await write_note(
        title="Context Root Note",
        content="This is the main note for context building",
        folder="context",
        project=second_project.permalink,
    )

    await write_note(
        title="Related Context Note",
        content="This is related to [[Context Root Note]]",
        folder="context",
        project=second_project.permalink,
    )

    # Build context from second project
    context_result = await build_context(
        url="memory://context/context-root-note", project=second_project.permalink
    )

    # Should contain context from the second project
    assert "Context Root Note" in str(context_result) or "context building" in str(context_result)
