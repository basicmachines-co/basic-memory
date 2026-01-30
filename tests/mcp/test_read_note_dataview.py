"""Tests for read_note Dataview integration.

These tests verify that read_note properly provides notes to the Dataview
integration for query execution.
"""

import pytest
from unittest.mock import patch, MagicMock

from basic_memory.mcp.tools import read_note, write_note


@pytest.mark.asyncio
async def test_read_note_dataview_receives_notes_provider(app, test_project):
    """Test that read_note passes a notes_provider to DataviewIntegration.
    
    This is the core test for the bug fix: read_note was calling
    create_dataview_integration() without a notes_provider, causing
    Dataview queries to return 0 results.
    """
    # Create a test note with a Dataview query
    content = """# Test Note

```dataview
TABLE status, priority
FROM "test"
WHERE type = "user-story"
```
"""
    await write_note.fn(
        project=test_project.name,
        title="Test Dataview Note",
        folder="test",
        content=content
    )
    
    with patch('basic_memory.mcp.tools.read_note.create_dataview_integration') as mock_create:
        # Setup mock to return a mock integration
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = []
        mock_create.return_value = mock_integration
        
        # Call read_note with dataview enabled
        await read_note.fn(
            identifier="Test Dataview Note",
            project=test_project.name,
            enable_dataview=True
        )
        
        # Verify create_dataview_integration was called with a notes_provider
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        
        # The notes_provider should be passed as a keyword argument
        assert call_kwargs.kwargs is not None
        assert 'notes_provider' in call_kwargs.kwargs
        notes_provider = call_kwargs.kwargs['notes_provider']
        
        # notes_provider should be callable
        assert callable(notes_provider)
        
        # notes_provider should return a list of notes
        notes = notes_provider()
        assert isinstance(notes, list)


@pytest.mark.asyncio
async def test_read_note_dataview_notes_have_required_fields(app, test_project):
    """Test that notes provided to Dataview have the required fields.
    
    Dataview expects notes with specific fields:
    - file.path, file.name, file.folder
    - title
    - type (entity_type)
    - permalink (optional)
    - frontmatter fields (optional)
    """
    # Create a test note
    await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="test",
        content="# Test Note\n\nTest content"
    )
    
    captured_notes = []
    
    def capture_notes_provider(notes_provider=None):
        """Capture the notes_provider and return a mock integration."""
        if notes_provider:
            captured_notes.extend(notes_provider())
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = []
        return mock_integration
    
    with patch('basic_memory.mcp.tools.read_note.create_dataview_integration', 
               side_effect=capture_notes_provider):
        await read_note.fn(
            identifier="Test Note",
            project=test_project.name,
            enable_dataview=True
        )
    
    # Should have captured some notes
    assert len(captured_notes) > 0, "No notes were provided to Dataview"
    
    # Verify each note has required fields
    for note in captured_notes:
        # file object with path, name, folder
        assert 'file' in note, f"Note missing 'file' field: {note}"
        assert 'path' in note['file'], f"Note missing 'file.path': {note}"
        assert 'name' in note['file'], f"Note missing 'file.name': {note}"
        assert 'folder' in note['file'], f"Note missing 'file.folder': {note}"
        
        # title is required
        assert 'title' in note, f"Note missing 'title': {note}"
        
        # type (entity_type) is required
        assert 'type' in note, f"Note missing 'type': {note}"


@pytest.mark.asyncio
async def test_read_note_dataview_disabled_no_notes_fetch(app, test_project):
    """Test that when enable_dataview=False, no notes are fetched."""
    # Create a test note
    await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="test",
        content="# Test Note"
    )
    
    with patch('basic_memory.mcp.tools.read_note.create_dataview_integration') as mock_create:
        # Call read_note with dataview disabled
        await read_note.fn(
            identifier="Test Note",
            project=test_project.name,
            enable_dataview=False
        )
        
        # create_dataview_integration should not be called
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_read_note_dataview_results_included_in_content(app, test_project):
    """Test that Dataview query results are included in the returned content.
    
    This tests the fix for build_context which was only adding a summary
    instead of the full result_markdown.
    """
    # Create a test note with a Dataview query
    content = """# Test Note

```dataview
TABLE status, priority
FROM "test"
WHERE type = "user-story"
```
"""
    await write_note.fn(
        project=test_project.name,
        title="Test Dataview Note",
        folder="test",
        content=content
    )
    
    # Mock the integration to return a result with markdown
    def mock_create_integration(notes_provider=None):
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = [
            {
                'query_id': 1,
                'line_number': 3,
                'query_type': 'TABLE',
                'status': 'success',
                'execution_time_ms': 10,
                'result_count': 2,
                'result_markdown': '| Title | Status | Priority |\n|-------|--------|----------|\n| US-001 | Done | P0 |\n| US-002 | In Progress | P1 |',
                'discovered_links': []
            }
        ]
        return mock_integration
    
    with patch('basic_memory.mcp.tools.read_note.create_dataview_integration',
               side_effect=mock_create_integration):
        result = await read_note.fn(
            identifier="Test Dataview Note",
            project=test_project.name,
            enable_dataview=True
        )
        
        # Verify the result contains the markdown table
        assert '| Title | Status | Priority |' in result
        assert '| US-001 | Done | P0 |' in result
        assert '| US-002 | In Progress | P1 |' in result
        
        # Verify it's in a Dataview section
        assert '## Dataview Query Results' in result
