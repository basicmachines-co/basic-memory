"""Tests for Dataview Parser."""

import pytest

from basic_memory.dataview.ast import (
    BinaryOpNode,
    DataviewQuery,
    FieldNode,
    FunctionCallNode,
    LiteralNode,
    QueryType,
    SortDirection,
    TableField,
)
from basic_memory.dataview.errors import DataviewSyntaxError
from basic_memory.dataview.parser import DataviewParser


class TestParserQueryTypes:
    """Test parsing different query types."""

    def test_parse_list_simple(self):
        """Test parsing simple LIST query."""
        query = DataviewParser.parse("LIST")
        assert query.query_type == QueryType.LIST
        assert query.from_source is None
        assert query.where_clause is None

    def test_parse_task_simple(self):
        """Test parsing simple TASK query."""
        query = DataviewParser.parse("TASK")
        assert query.query_type == QueryType.TASK

    def test_parse_table_simple(self):
        """Test parsing simple TABLE query."""
        query = DataviewParser.parse("TABLE title")
        assert query.query_type == QueryType.TABLE
        assert query.fields is not None
        assert len(query.fields) == 1

    def test_parse_calendar_simple(self):
        """Test parsing simple CALENDAR query."""
        query = DataviewParser.parse("CALENDAR")
        assert query.query_type == QueryType.CALENDAR

    def test_error_on_invalid_query_type(self):
        """Test error on invalid query type."""
        with pytest.raises(DataviewSyntaxError, match="Expected query type"):
            DataviewParser.parse("INVALID")


class TestParserFromClause:
    """Test parsing FROM clauses."""

    def test_parse_from_folder(self):
        """Test FROM with folder path."""
        query = DataviewParser.parse('LIST FROM "1. projects"')
        assert query.from_source == "1. projects"

    def test_parse_from_tag(self):
        """Test FROM with tag."""
        query = DataviewParser.parse("LIST FROM #project")
        assert query.from_source == "#project"

    def test_parse_from_identifier(self):
        """Test FROM with identifier."""
        query = DataviewParser.parse("LIST FROM projects")
        assert query.from_source == "projects"

    def test_parse_from_with_single_quotes(self):
        """Test FROM with single quotes."""
        query = DataviewParser.parse("LIST FROM '1. projects'")
        assert query.from_source == "1. projects"


class TestParserTableFields:
    """Test parsing TABLE fields."""

    def test_parse_single_field(self):
        """Test parsing single field."""
        query = DataviewParser.parse("TABLE title")
        assert len(query.fields) == 1
        assert isinstance(query.fields[0].expression, FieldNode)
        assert query.fields[0].expression.field_name == "title"

    def test_parse_multiple_fields(self):
        """Test parsing multiple fields."""
        query = DataviewParser.parse("TABLE title, status, priority")
        assert len(query.fields) == 3
        assert query.fields[0].expression.field_name == "title"
        assert query.fields[1].expression.field_name == "status"
        assert query.fields[2].expression.field_name == "priority"

    def test_parse_field_with_alias(self):
        """Test parsing field with alias."""
        query = DataviewParser.parse('TABLE title AS "Project Name"')
        assert len(query.fields) == 1
        assert query.fields[0].alias == "Project Name"

    def test_parse_field_path(self):
        """Test parsing field path (e.g., file.name)."""
        query = DataviewParser.parse("TABLE file.name")
        assert query.fields[0].expression.field_name == "file.name"

    def test_parse_without_id(self):
        """Test parsing WITHOUT ID."""
        query = DataviewParser.parse("TABLE WITHOUT ID title, status")
        assert len(query.fields) == 2


class TestParserWhereClause:
    """Test parsing WHERE clauses."""

    def test_parse_where_equals(self):
        """Test WHERE with equals."""
        query = DataviewParser.parse('LIST WHERE status = "active"')
        assert query.where_clause is not None
        expr = query.where_clause.expression
        assert isinstance(expr, BinaryOpNode)
        assert expr.operator == "="

    def test_parse_where_not_equals(self):
        """Test WHERE with not equals."""
        query = DataviewParser.parse('LIST WHERE status != "archived"')
        expr = query.where_clause.expression
        assert isinstance(expr, BinaryOpNode)
        assert expr.operator == "!="

    def test_parse_where_less_than(self):
        """Test WHERE with less than."""
        query = DataviewParser.parse("LIST WHERE priority < 3")
        expr = query.where_clause.expression
        assert expr.operator == "<"

    def test_parse_where_greater_than(self):
        """Test WHERE with greater than."""
        query = DataviewParser.parse("LIST WHERE priority > 1")
        expr = query.where_clause.expression
        assert expr.operator == ">"

    def test_parse_where_less_equal(self):
        """Test WHERE with less or equal."""
        query = DataviewParser.parse("LIST WHERE priority <= 3")
        expr = query.where_clause.expression
        assert expr.operator == "<="

    def test_parse_where_greater_equal(self):
        """Test WHERE with greater or equal."""
        query = DataviewParser.parse("LIST WHERE priority >= 1")
        expr = query.where_clause.expression
        assert expr.operator == ">="

    def test_parse_where_with_and(self):
        """Test WHERE with AND."""
        query = DataviewParser.parse('LIST WHERE status = "active" AND priority > 1')
        expr = query.where_clause.expression
        assert isinstance(expr, BinaryOpNode)
        assert expr.operator == "AND"

    def test_parse_where_with_or(self):
        """Test WHERE with OR."""
        query = DataviewParser.parse('LIST WHERE status = "active" OR status = "pending"')
        expr = query.where_clause.expression
        assert expr.operator == "OR"

    def test_parse_where_with_function(self):
        """Test WHERE with function call."""
        query = DataviewParser.parse('LIST WHERE contains(tags, "bug")')
        expr = query.where_clause.expression
        assert isinstance(expr, FunctionCallNode)
        assert expr.function_name == "contains"
        assert len(expr.arguments) == 2

    def test_parse_where_with_parentheses(self):
        """Test WHERE with parentheses."""
        query = DataviewParser.parse('LIST WHERE (status = "active" OR status = "pending") AND priority > 1')
        expr = query.where_clause.expression
        assert isinstance(expr, BinaryOpNode)
        assert expr.operator == "AND"


class TestParserSortClause:
    """Test parsing SORT clauses."""

    def test_parse_sort_single_field(self):
        """Test SORT with single field."""
        query = DataviewParser.parse("LIST SORT title")
        assert len(query.sort_clauses) == 1
        assert query.sort_clauses[0].field == "title"
        assert query.sort_clauses[0].direction == SortDirection.ASC

    def test_parse_sort_with_asc(self):
        """Test SORT with explicit ASC."""
        query = DataviewParser.parse("LIST SORT title ASC")
        assert query.sort_clauses[0].direction == SortDirection.ASC

    def test_parse_sort_with_desc(self):
        """Test SORT with DESC."""
        query = DataviewParser.parse("LIST SORT title DESC")
        assert query.sort_clauses[0].direction == SortDirection.DESC

    def test_parse_sort_multiple_fields(self):
        """Test SORT with multiple fields."""
        query = DataviewParser.parse("LIST SORT priority ASC, title DESC")
        assert len(query.sort_clauses) == 2
        assert query.sort_clauses[0].field == "priority"
        assert query.sort_clauses[0].direction == SortDirection.ASC
        assert query.sort_clauses[1].field == "title"
        assert query.sort_clauses[1].direction == SortDirection.DESC

    def test_parse_sort_field_path(self):
        """Test SORT with field path."""
        query = DataviewParser.parse("LIST SORT file.name")
        assert query.sort_clauses[0].field == "file.name"


class TestParserLimitClause:
    """Test parsing LIMIT clauses."""

    def test_parse_limit(self):
        """Test LIMIT clause."""
        query = DataviewParser.parse("LIST LIMIT 10")
        assert query.limit == 10

    def test_parse_limit_with_large_number(self):
        """Test LIMIT with large number."""
        query = DataviewParser.parse("LIST LIMIT 1000")
        assert query.limit == 1000

    def test_error_on_limit_without_number(self):
        """Test error on LIMIT without number."""
        with pytest.raises(DataviewSyntaxError, match="Expected number after LIMIT"):
            DataviewParser.parse("LIST LIMIT")


class TestParserExpressions:
    """Test parsing expressions."""

    def test_parse_string_literal(self):
        """Test parsing string literal."""
        query = DataviewParser.parse('TABLE "hello"')
        expr = query.fields[0].expression
        assert isinstance(expr, LiteralNode)
        assert expr.value == "hello"

    def test_parse_number_literal(self):
        """Test parsing number literal."""
        query = DataviewParser.parse("TABLE 42")
        expr = query.fields[0].expression
        assert isinstance(expr, LiteralNode)
        assert expr.value == 42

    def test_parse_float_literal(self):
        """Test parsing float literal."""
        query = DataviewParser.parse("TABLE 3.14")
        expr = query.fields[0].expression
        assert isinstance(expr, LiteralNode)
        assert expr.value == 3.14

    def test_parse_boolean_true(self):
        """Test parsing true literal."""
        query = DataviewParser.parse("TABLE true")
        expr = query.fields[0].expression
        assert isinstance(expr, LiteralNode)
        assert expr.value is True

    def test_parse_boolean_false(self):
        """Test parsing false literal."""
        query = DataviewParser.parse("TABLE false")
        expr = query.fields[0].expression
        assert isinstance(expr, LiteralNode)
        assert expr.value is False

    def test_parse_null_literal(self):
        """Test parsing null literal."""
        query = DataviewParser.parse("TABLE null")
        expr = query.fields[0].expression
        assert isinstance(expr, LiteralNode)
        assert expr.value is None

    def test_parse_field_reference(self):
        """Test parsing field reference."""
        query = DataviewParser.parse("TABLE status")
        expr = query.fields[0].expression
        assert isinstance(expr, FieldNode)
        assert expr.field_name == "status"

    def test_parse_function_call(self):
        """Test parsing function call."""
        query = DataviewParser.parse('TABLE length(tags)')
        expr = query.fields[0].expression
        assert isinstance(expr, FunctionCallNode)
        assert expr.function_name == "length"
        assert len(expr.arguments) == 1

    def test_parse_function_with_multiple_args(self):
        """Test parsing function with multiple arguments."""
        query = DataviewParser.parse('TABLE contains(tags, "bug")')
        expr = query.fields[0].expression
        assert isinstance(expr, FunctionCallNode)
        assert len(expr.arguments) == 2


class TestParserComplexQueries:
    """Test parsing complex queries."""

    def test_parse_full_query(self):
        """Test parsing full query with all clauses."""
        query = DataviewParser.parse(
            'TABLE title, status FROM "1. projects" WHERE status = "active" SORT title ASC LIMIT 10'
        )
        assert query.query_type == QueryType.TABLE
        assert len(query.fields) == 2
        assert query.from_source == "1. projects"
        assert query.where_clause is not None
        assert len(query.sort_clauses) == 1
        assert query.limit == 10

    def test_parse_complex_where(self):
        """Test parsing complex WHERE clause."""
        query = DataviewParser.parse(
            'LIST WHERE (status = "active" OR status = "pending") AND priority > 1 AND contains(tags, "urgent")'
        )
        assert query.where_clause is not None

    def test_parse_table_with_aliases(self):
        """Test parsing TABLE with aliases."""
        query = DataviewParser.parse('TABLE title AS "Name", status AS "Status"')
        assert query.fields[0].alias == "Name"
        assert query.fields[1].alias == "Status"


class TestParserErrors:
    """Test error handling."""

    def test_error_on_empty_query(self):
        """Test error on empty query."""
        with pytest.raises(DataviewSyntaxError):
            DataviewParser.parse("")

    def test_error_on_invalid_syntax(self):
        """Test error on invalid syntax."""
        with pytest.raises(DataviewSyntaxError):
            DataviewParser.parse("LIST FROM")

    def test_error_on_missing_field(self):
        """Test error on missing field in TABLE."""
        with pytest.raises(DataviewSyntaxError):
            DataviewParser.parse("TABLE FROM")


class TestParserEdgeCases:
    """Test edge cases."""

    def test_parse_query_with_extra_whitespace(self):
        """Test parsing query with extra whitespace."""
        query = DataviewParser.parse("LIST    FROM    '1. projects'")
        assert query.from_source == "1. projects"

    def test_parse_query_with_newlines(self):
        """Test parsing query with newlines."""
        query = DataviewParser.parse("LIST\nFROM\n'1. projects'")
        assert query.from_source == "1. projects"

    def test_parse_empty_table_fields(self):
        """Test parsing TABLE without fields (should default to all fields)."""
        query = DataviewParser.parse("TABLE")
        assert query.query_type == QueryType.TABLE
        assert query.fields == []  # Empty fields means show all
