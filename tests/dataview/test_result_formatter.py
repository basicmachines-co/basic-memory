"""Tests for ResultFormatter."""

import pytest

from basic_memory.dataview.executor.result_formatter import ResultFormatter


class TestResultFormatterTable:
    """Test formatting table results."""

    def test_format_simple_table(self):
        """Test formatting simple table."""
        results = [
            {"title": "Note 1", "status": "active"},
            {"title": "Note 2", "status": "archived"},
        ]
        fields = ["title", "status"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| title | status |" in output
        assert "| --- | --- |" in output
        assert "| Note 1 | active |" in output
        assert "| Note 2 | archived |" in output

    def test_format_table_with_numbers(self):
        """Test formatting table with numbers."""
        results = [
            {"title": "Note 1", "priority": 1},
            {"title": "Note 2", "priority": 2},
        ]
        fields = ["title", "priority"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| Note 1 | 1 |" in output
        assert "| Note 2 | 2 |" in output

    def test_format_table_with_booleans(self):
        """Test formatting table with booleans."""
        results = [
            {"title": "Note 1", "completed": True},
            {"title": "Note 2", "completed": False},
        ]
        fields = ["title", "completed"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| Note 1 | ✓ |" in output
        assert "| Note 2 | ✗ |" in output

    def test_format_table_with_lists(self):
        """Test formatting table with lists."""
        results = [
            {"title": "Note 1", "tags": ["tag1", "tag2"]},
            {"title": "Note 2", "tags": ["tag3"]},
        ]
        fields = ["title", "tags"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| Note 1 | tag1, tag2 |" in output
        assert "| Note 2 | tag3 |" in output

    def test_format_table_with_none_values(self):
        """Test formatting table with None values."""
        results = [
            {"title": "Note 1", "status": None},
            {"title": "Note 2", "status": "active"},
        ]
        fields = ["title", "status"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| Note 1 |  |" in output
        assert "| Note 2 | active |" in output

    def test_format_empty_table(self):
        """Test formatting empty table."""
        results = []
        fields = ["title", "status"]
        output = ResultFormatter.format_table(results, fields)
        
        assert output == "_No results_"


class TestResultFormatterList:
    """Test formatting list results."""

    def test_format_simple_list(self):
        """Test formatting simple list."""
        results = [
            {"file.link": "[[Note 1]]"},
            {"file.link": "[[Note 2]]"},
        ]
        output = ResultFormatter.format_list(results)
        
        assert "- [[Note 1]]" in output
        assert "- [[Note 2]]" in output

    def test_format_list_with_titles(self):
        """Test formatting list with titles."""
        results = [
            {"title": "Note 1"},
            {"title": "Note 2"},
        ]
        output = ResultFormatter.format_list(results, field="title")
        
        assert "- Note 1" in output
        assert "- Note 2" in output

    def test_format_empty_list(self):
        """Test formatting empty list."""
        results = []
        output = ResultFormatter.format_list(results)
        
        assert output == "_No results_"

    def test_format_list_fallback_to_title(self):
        """Test formatting list falls back to title."""
        results = [
            {"title": "Note 1"},
            {"title": "Note 2"},
        ]
        output = ResultFormatter.format_list(results)
        
        # Should fallback to title when file.link not present
        assert "Note 1" in output or "[[Note 1]]" in output


class TestResultFormatterTaskList:
    """Test formatting task lists."""

    def test_format_simple_task_list(self):
        """Test formatting simple task list."""
        tasks = [
            {"text": "Task 1", "completed": False, "indentation": 0},
            {"text": "Task 2", "completed": True, "indentation": 0},
        ]
        output = ResultFormatter.format_task_list(tasks)
        
        assert "- [ ] Task 1" in output
        assert "- [x] Task 2" in output

    def test_format_indented_task_list(self):
        """Test formatting indented task list."""
        tasks = [
            {"text": "Task 1", "completed": False, "indentation": 0},
            {"text": "Subtask 1.1", "completed": False, "indentation": 2},
            {"text": "Subtask 1.2", "completed": True, "indentation": 2},
        ]
        output = ResultFormatter.format_task_list(tasks)
        
        assert "- [ ] Task 1" in output
        assert "  - [ ] Subtask 1.1" in output
        assert "  - [x] Subtask 1.2" in output

    def test_format_empty_task_list(self):
        """Test formatting empty task list."""
        tasks = []
        output = ResultFormatter.format_task_list(tasks)
        
        assert output == "_No tasks_"

    def test_format_task_list_with_missing_fields(self):
        """Test formatting task list with missing fields."""
        tasks = [
            {"text": "Task 1"},  # Missing completed and indentation
        ]
        output = ResultFormatter.format_task_list(tasks)
        
        assert "- [ ] Task 1" in output


class TestResultFormatterEdgeCases:
    """Test edge cases."""

    def test_format_table_with_missing_fields(self):
        """Test formatting table with missing fields."""
        results = [
            {"title": "Note 1"},  # Missing status
            {"title": "Note 2", "status": "active"},
        ]
        fields = ["title", "status"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| Note 1 |  |" in output
        assert "| Note 2 | active |" in output

    def test_format_table_with_extra_fields(self):
        """Test formatting table with extra fields in results."""
        results = [
            {"title": "Note 1", "status": "active", "extra": "ignored"},
        ]
        fields = ["title", "status"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "| Note 1 | active |" in output
        assert "extra" not in output

    def test_format_list_with_unknown_field(self):
        """Test formatting list with unknown field."""
        results = [
            {"title": "Note 1"},
        ]
        output = ResultFormatter.format_list(results, field="nonexistent")
        
        # Should fallback to title
        assert "Note 1" in output or "Unknown" in output

    def test_format_table_with_empty_strings(self):
        """Test formatting table with empty strings."""
        results = [
            {"title": "", "status": ""},
        ]
        fields = ["title", "status"]
        output = ResultFormatter.format_table(results, fields)
        
        assert "|  |  |" in output
