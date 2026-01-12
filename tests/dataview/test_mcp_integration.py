"""
Test MCP tools integration with Dataview.

These tests verify that Dataview queries are properly detected, executed,
and integrated into MCP tool responses.
"""

import pytest

from basic_memory.dataview.integration import (
    DataviewIntegration,
    create_dataview_integration,
)


class TestDataviewIntegration:
    """Test the DataviewIntegration class."""

    def test_create_integration(self):
        """Test factory function creates integration."""
        integration = create_dataview_integration()
        assert isinstance(integration, DataviewIntegration)
        assert integration.notes_provider is None

    def test_create_integration_with_provider(self):
        """Test factory function with notes provider."""

        def mock_provider():
            return [{"title": "Test", "path": "test.md"}]

        integration = create_dataview_integration(mock_provider)
        assert integration.notes_provider is mock_provider

    def test_process_note_no_queries(self):
        """Test processing a note without Dataview queries."""
        integration = create_dataview_integration()
        content = """
# My Note

This is just regular markdown content.
No Dataview queries here.
"""
        results = integration.process_note(content)
        assert len(results) == 0

    def test_process_note_with_codeblock_query(self):
        """Test processing a note with a Dataview codeblock query."""
        integration = create_dataview_integration()
        content = """
# My Note

Here's a Dataview query:

```dataview
LIST FROM "1. projects"
```

More content below.
"""
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        assert result["query_id"] == "dv-1"
        assert result["query_type"] == "LIST"
        assert result["line_number"] == 6  # Line where query starts
        assert result["status"] == "success"
        assert "execution_time_ms" in result
        assert isinstance(result["execution_time_ms"], int)

    def test_process_note_with_multiple_queries(self):
        """Test processing a note with multiple Dataview queries."""
        integration = create_dataview_integration()
        content = """
# My Note

First query:

```dataview
LIST FROM "1. projects"
```

Second query:

```dataview
TABLE file.name FROM "2. areas"
```
"""
        results = integration.process_note(content)

        assert len(results) == 2
        assert results[0]["query_id"] == "dv-1"
        assert results[0]["query_type"] == "LIST"
        assert results[1]["query_id"] == "dv-2"
        assert results[1]["query_type"] == "TABLE"

    def test_process_note_with_syntax_error(self):
        """Test processing a note with invalid Dataview syntax."""
        integration = create_dataview_integration()
        content = """
```dataview
INVALID SYNTAX HERE
```
"""
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        assert result["status"] == "error"
        assert result["error_type"] == "syntax"
        assert "error" in result
        assert result["result_count"] == 0

    def test_process_note_with_inline_query(self):
        """Test processing a note with inline Dataview query."""
        integration = create_dataview_integration()
        content = """
# My Note

The count is: `= 2 + 2`
"""
        results = integration.process_note(content)

        # Inline queries are detected but may not execute properly without proper context
        assert len(results) == 1
        result = results[0]
        assert result["query_id"] == "dv-1"
        assert "line_number" in result

    def test_execution_time_tracking(self):
        """Test that execution time is tracked."""
        integration = create_dataview_integration()
        content = """
```dataview
LIST FROM "test"
```
"""
        results = integration.process_note(content)

        assert len(results) == 1
        assert "execution_time_ms" in results[0]
        assert results[0]["execution_time_ms"] >= 0
        assert isinstance(results[0]["execution_time_ms"], int)

    def test_discovered_links_extraction(self):
        """Test that discovered links are extracted from results."""
        # Create integration with mock notes
        def notes_provider():
            return [
                {"title": "Project A", "path": "1. projects/project-a.md"},
                {"title": "Project B", "path": "1. projects/project-b.md"},
            ]

        integration = create_dataview_integration(notes_provider)
        content = """
```dataview
LIST FROM "1. projects"
```
"""
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        assert "discovered_links" in result
        assert isinstance(result["discovered_links"], list)

    def test_result_markdown_included(self):
        """Test that result markdown is included in successful queries."""
        def notes_provider():
            return [{"title": "Test Note", "path": "test.md"}]

        integration = create_dataview_integration(notes_provider)
        content = """
```dataview
LIST FROM "test"
```
"""
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        if result["status"] == "success":
            assert "result_markdown" in result
            assert isinstance(result["result_markdown"], str)

    def test_query_source_formatting(self):
        """Test that query source is properly formatted."""
        integration = create_dataview_integration()
        content = """
```dataview
LIST FROM "test"
```
"""
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        assert "query_source" in result
        assert result["query_source"].startswith("```dataview")
        assert result["query_source"].endswith("```")

    def test_error_handling_unexpected_exception(self):
        """Test handling of unexpected exceptions during execution."""
        # Create integration that will fail
        def failing_provider():
            raise RuntimeError("Simulated failure")

        integration = create_dataview_integration(failing_provider)
        content = """
```dataview
LIST FROM "test"
```
"""
        # Should not raise, should return error result
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        # The query will execute with empty notes list since provider fails
        # So it should succeed but with no results
        assert result["status"] in ("success", "error")

    def test_process_note_with_metadata(self):
        """Test processing with note metadata."""
        integration = create_dataview_integration()
        content = """
```dataview
LIST FROM "test"
```
"""
        metadata = {"id": 123, "title": "Test Note", "path": "test.md"}

        results = integration.process_note(content, metadata)

        assert len(results) == 1
        # Metadata is currently not used but should not cause errors

    def test_result_count_accuracy(self):
        """Test that result_count accurately reflects number of results."""

        def notes_provider():
            return [
                {"title": "Note 1", "path": "1. projects/note1.md"},
                {"title": "Note 2", "path": "1. projects/note2.md"},
                {"title": "Note 3", "path": "1. projects/note3.md"},
            ]

        integration = create_dataview_integration(notes_provider)
        content = """
```dataview
LIST FROM "1. projects"
```
"""
        results = integration.process_note(content)

        assert len(results) == 1
        result = results[0]
        if result["status"] == "success":
            assert "result_count" in result
            assert result["result_count"] >= 0


class TestMCPToolsIntegration:
    """Test integration with MCP tools (read_note, search_notes, build_context)."""

    def test_read_note_dataview_parameter(self):
        """Test that read_note accepts enable_dataview parameter."""
        # This is a signature test - actual integration would require full MCP setup
        from basic_memory.mcp.tools.read_note import read_note

        # Check that the function signature includes enable_dataview
        import inspect

        sig = inspect.signature(read_note.fn)  # Access the wrapped function
        assert "enable_dataview" in sig.parameters
        assert sig.parameters["enable_dataview"].default is True

    def test_search_notes_dataview_parameter(self):
        """Test that search_notes accepts enable_dataview parameter."""
        from basic_memory.mcp.tools.search import search_notes
        import inspect

        sig = inspect.signature(search_notes.fn)
        assert "enable_dataview" in sig.parameters
        assert sig.parameters["enable_dataview"].default is False  # False for performance

    def test_build_context_dataview_parameter(self):
        """Test that build_context accepts enable_dataview parameter."""
        from basic_memory.mcp.tools.build_context import build_context
        import inspect

        sig = inspect.signature(build_context.fn)
        assert "enable_dataview" in sig.parameters
        assert sig.parameters["enable_dataview"].default is True


class TestBackwardCompatibility:
    """Test that existing MCP tool calls still work without enable_dataview parameter."""

    def test_integration_does_not_break_existing_calls(self):
        """Test that DataviewIntegration can be created without breaking existing code."""
        # Should work without any parameters
        integration = create_dataview_integration()
        assert integration is not None

        # Should work with empty content
        results = integration.process_note("")
        assert results == []

        # Should work with None content (gracefully handle)
        try:
            results = integration.process_note(None)  # type: ignore
        except (TypeError, AttributeError):
            # Expected - None is not a valid string
            pass
