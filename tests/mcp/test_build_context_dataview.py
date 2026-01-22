"""Tests for build_context Dataview integration.

These tests verify that build_context properly provides notes to the Dataview
integration for query execution.
"""

import pytest
from unittest.mock import patch, MagicMock

from basic_memory.mcp.tools import build_context
from basic_memory.schemas.memory import GraphContext


@pytest.mark.asyncio
async def test_build_context_dataview_receives_notes_provider(client, test_graph, test_project):
    """Test that build_context passes a notes_provider to DataviewIntegration.
    
    This is the core test for the bug fix: build_context was calling
    create_dataview_integration() without a notes_provider, causing
    Dataview queries to return 0 results.
    """
    with patch('basic_memory.mcp.tools.build_context.create_dataview_integration') as mock_create:
        # Setup mock to return a mock integration
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = []
        mock_create.return_value = mock_integration
        
        # Call build_context with dataview enabled
        await build_context.fn(
            project=test_project.name,
            url="memory://test/root",
            enable_dataview=True
        )
        
        # Verify create_dataview_integration was called with a notes_provider
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        
        # The notes_provider should be passed as a keyword argument or positional
        if call_kwargs.kwargs:
            assert 'notes_provider' in call_kwargs.kwargs
            notes_provider = call_kwargs.kwargs['notes_provider']
        else:
            # Positional argument
            assert len(call_kwargs.args) > 0
            notes_provider = call_kwargs.args[0]
        
        # notes_provider should be callable
        assert callable(notes_provider)
        
        # notes_provider should return a list of notes
        notes = notes_provider()
        assert isinstance(notes, list)


@pytest.mark.asyncio
async def test_build_context_dataview_notes_have_required_fields(client, test_graph, test_project):
    """Test that notes provided to Dataview have the required fields.
    
    Dataview expects notes with specific fields:
    - file.path, file.name, file.folder
    - title
    - type (entity_type)
    - permalink (optional)
    - frontmatter fields (optional)
    """
    captured_notes = []
    
    def capture_notes_provider(notes_provider=None):
        """Capture the notes_provider and return a mock integration."""
        if notes_provider:
            captured_notes.extend(notes_provider())
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = []
        return mock_integration
    
    with patch('basic_memory.mcp.tools.build_context.create_dataview_integration', 
               side_effect=capture_notes_provider):
        await build_context.fn(
            project=test_project.name,
            url="memory://test/root",
            enable_dataview=True
        )
    
    # Should have captured some notes (from test_graph fixture)
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
async def test_build_context_dataview_disabled_no_notes_fetch(client, test_graph, test_project):
    """Test that when enable_dataview=False, no notes are fetched."""
    with patch('basic_memory.mcp.tools.build_context.create_dataview_integration') as mock_create:
        # Call build_context with dataview disabled
        await build_context.fn(
            project=test_project.name,
            url="memory://test/root",
            enable_dataview=False
        )
        
        # create_dataview_integration should not be called
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_build_context_dataview_notes_count_matches_entities(
    client, test_graph, test_project, entity_repository
):
    """Test that the number of notes matches the number of entities in the project."""
    captured_notes = []
    
    def capture_notes_provider(notes_provider=None):
        if notes_provider:
            captured_notes.extend(notes_provider())
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = []
        return mock_integration
    
    with patch('basic_memory.mcp.tools.build_context.create_dataview_integration',
               side_effect=capture_notes_provider):
        await build_context.fn(
            project=test_project.name,
            url="memory://test/root",
            enable_dataview=True
        )
    
    # Get actual entity count from repository
    all_entities = await entity_repository.find_all()
    
    # Notes count should match entity count
    assert len(captured_notes) == len(all_entities), \
        f"Expected {len(all_entities)} notes, got {len(captured_notes)}"


@pytest.mark.asyncio
async def test_build_context_dataview_empty_results_still_provides_notes(client, test_graph, test_project):
    """Test that even when build_context returns no results, notes are still provided to Dataview."""
    captured_notes = []
    
    def capture_notes_provider(notes_provider=None):
        if notes_provider:
            captured_notes.extend(notes_provider())
        mock_integration = MagicMock()
        mock_integration.process_note.return_value = []
        return mock_integration
    
    with patch('basic_memory.mcp.tools.build_context.create_dataview_integration',
               side_effect=capture_notes_provider):
        # Query for non-existent path - should return empty results
        result = await build_context.fn(
            project=test_project.name,
            url="memory://nonexistent/path",
            enable_dataview=True
        )
    
    # Results should be empty
    assert len(result.results) == 0
    
    # But notes should still be provided for Dataview queries in the content
    # (even though there's no content to process in this case)
    # The notes_provider should still be set up correctly
    assert len(captured_notes) > 0, "Notes should be provided even for empty results"
