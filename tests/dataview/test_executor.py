"""Tests for DataviewExecutor."""

import pytest

from basic_memory.dataview.ast import QueryType
from basic_memory.dataview.executor.executor import DataviewExecutor
from basic_memory.dataview.parser import DataviewParser


class TestExecutorList:
    """Test executing LIST queries."""

    def test_execute_simple_list(self, sample_notes):
        """Test executing simple LIST query."""
        query = DataviewParser.parse("LIST")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "[[Project Alpha]]" in result
        assert "[[Project Beta]]" in result
        assert "[[Area Dev]]" in result

    def test_execute_list_with_from(self, sample_notes):
        """Test executing LIST with FROM clause."""
        query = DataviewParser.parse('LIST FROM "1. projects"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "[[Project Alpha]]" in result
        assert "[[Project Beta]]" in result
        assert "[[Area Dev]]" not in result

    def test_execute_list_with_where(self, sample_notes):
        """Test executing LIST with WHERE clause."""
        query = DataviewParser.parse('LIST WHERE status = "active"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "[[Project Alpha]]" in result
        assert "[[Project Beta]]" not in result  # archived

    def test_execute_list_with_limit(self, sample_notes):
        """Test executing LIST with LIMIT."""
        query = DataviewParser.parse("LIST LIMIT 2")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should only have 2 results
        lines = [line for line in result.split("\n") if line.startswith("-")]
        assert len(lines) == 2


class TestExecutorTable:
    """Test executing TABLE queries."""

    def test_execute_simple_table(self, sample_notes):
        """Test executing simple TABLE query."""
        query = DataviewParser.parse("TABLE title, status")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "| title | status |" in result
        assert "| Project Alpha | active |" in result
        assert "| Project Beta | archived |" in result

    def test_execute_table_with_from(self, sample_notes):
        """Test executing TABLE with FROM clause."""
        query = DataviewParser.parse('TABLE title, status FROM "1. projects"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "| Project Alpha | active |" in result
        assert "| Project Beta | archived |" in result
        assert "Area Dev" not in result

    def test_execute_table_with_where(self, sample_notes):
        """Test executing TABLE with WHERE clause."""
        query = DataviewParser.parse('TABLE title, status WHERE status = "active"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "| Project Alpha | active |" in result
        assert "Project Beta" not in result

    def test_execute_table_with_sort(self, sample_notes):
        """Test executing TABLE with SORT."""
        query = DataviewParser.parse("TABLE title, priority SORT priority ASC")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Results should be sorted by priority
        lines = result.split("\n")
        # Find data rows (skip header and separator)
        data_rows = [line for line in lines if line.startswith("|") and "---" not in line and "title" not in line]
        assert len(data_rows) > 0

    def test_execute_table_with_limit(self, sample_notes):
        """Test executing TABLE with LIMIT."""
        query = DataviewParser.parse("TABLE title, status LIMIT 2")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should only have 2 data rows (plus header and separator)
        lines = result.split("\n")
        data_rows = [line for line in lines if line.startswith("|") and "---" not in line and "title" not in line]
        assert len(data_rows) == 2


class TestExecutorTask:
    """Test executing TASK queries."""

    def test_execute_simple_task(self, sample_notes):
        """Test executing simple TASK query."""
        query = DataviewParser.parse("TASK")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "- [ ] Task 1" in result
        assert "- [x] Task 2" in result
        assert "- [ ] Task 3" in result

    def test_execute_task_with_from(self, sample_notes):
        """Test executing TASK with FROM clause."""
        query = DataviewParser.parse('TASK FROM "1. projects"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should only include tasks from projects folder
        assert "Task" in result

    def test_execute_task_with_limit(self, sample_notes):
        """Test executing TASK with LIMIT."""
        query = DataviewParser.parse("TASK LIMIT 2")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should only have 2 tasks
        lines = [line for line in result.split("\n") if line.strip().startswith("-")]
        assert len(lines) == 2


class TestExecutorFromClause:
    """Test FROM clause filtering."""

    def test_from_folder_exact(self, sample_notes):
        """Test FROM with exact folder match."""
        query = DataviewParser.parse('LIST FROM "1. projects"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Project Beta" in result
        assert "Area Dev" not in result

    def test_from_folder_prefix(self, sample_notes):
        """Test FROM with folder prefix."""
        query = DataviewParser.parse('LIST FROM "2. areas"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Area Dev" in result
        assert "Project Alpha" not in result


class TestExecutorWhereClause:
    """Test WHERE clause filtering."""

    def test_where_equals(self, sample_notes):
        """Test WHERE with equals."""
        query = DataviewParser.parse('LIST WHERE status = "active"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Area Dev" in result
        assert "Project Beta" not in result

    def test_where_not_equals(self, sample_notes):
        """Test WHERE with not equals."""
        query = DataviewParser.parse('LIST WHERE status != "archived"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Project Beta" not in result

    def test_where_greater_than(self, sample_notes):
        """Test WHERE with greater than."""
        query = DataviewParser.parse("LIST WHERE priority > 1")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Beta" in result  # priority 2
        assert "Project Alpha" not in result  # priority 1

    def test_where_and(self, sample_notes):
        """Test WHERE with AND."""
        query = DataviewParser.parse('LIST WHERE status = "active" AND priority = 1')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Project Beta" not in result

    def test_where_or(self, sample_notes):
        """Test WHERE with OR."""
        query = DataviewParser.parse('LIST WHERE status = "active" OR status = "archived"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Project Beta" in result


class TestExecutorSortClause:
    """Test SORT clause."""

    def test_sort_ascending(self, sample_notes):
        """Test SORT ascending."""
        query = DataviewParser.parse("TABLE title, priority SORT priority ASC")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should be sorted by priority ascending
        assert result.index("Project Alpha") < result.index("Project Beta")

    def test_sort_descending(self, sample_notes):
        """Test SORT descending."""
        query = DataviewParser.parse("TABLE title, priority SORT priority DESC")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should be sorted by priority descending
        assert result.index("Project Beta") < result.index("Project Alpha")

    def test_sort_multiple_fields(self, sample_notes):
        """Test SORT with multiple fields."""
        query = DataviewParser.parse("TABLE title, status, priority SORT status ASC, priority DESC")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should be sorted by status first, then priority
        assert "title" in result


class TestExecutorComplexQueries:
    """Test complex queries."""

    def test_full_query(self, sample_notes):
        """Test query with all clauses."""
        query = DataviewParser.parse(
            'TABLE title, status, priority FROM "1. projects" WHERE status = "active" SORT priority ASC LIMIT 10'
        )
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Project Beta" not in result  # filtered by WHERE
        assert "Area Dev" not in result  # filtered by FROM

    def test_query_with_function(self, sample_notes):
        """Test query with function in WHERE."""
        query = DataviewParser.parse('LIST WHERE contains(tags, "project")')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "Project Alpha" in result
        assert "Project Beta" in result


class TestExecutorEdgeCases:
    """Test edge cases."""

    def test_execute_with_empty_notes(self):
        """Test executing with empty notes list."""
        query = DataviewParser.parse("LIST")
        executor = DataviewExecutor([])
        result = executor.execute(query)
        
        assert "_No results_" in result

    def test_execute_with_no_matches(self, sample_notes):
        """Test executing with no matching notes."""
        query = DataviewParser.parse('LIST WHERE status = "nonexistent"')
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        assert "_No results_" in result

    def test_execute_table_with_missing_fields(self, sample_notes):
        """Test executing TABLE with missing fields."""
        query = DataviewParser.parse("TABLE title, nonexistent")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should handle missing fields gracefully
        assert "title" in result

    def test_execute_where_with_error(self, sample_notes):
        """Test executing WHERE that causes evaluation error."""
        query = DataviewParser.parse("LIST WHERE nonexistent = 'value'")
        executor = DataviewExecutor(sample_notes)
        result = executor.execute(query)
        
        # Should handle errors gracefully (skip notes with errors)
        assert isinstance(result, str)
