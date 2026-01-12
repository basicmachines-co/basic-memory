"""Tests for Dataview Lexer."""

import pytest

from basic_memory.dataview.lexer import DataviewLexer, Token, TokenType


class TestLexerBasics:
    """Test basic tokenization."""

    def test_tokenize_simple_task(self):
        """Test tokenizing a simple TASK query."""
        lexer = DataviewLexer("TASK")
        tokens = lexer.tokenize()
        assert len(tokens) == 2  # TASK + EOF
        assert tokens[0].type == TokenType.TASK
        assert tokens[1].type == TokenType.EOF

    def test_tokenize_simple_list(self):
        """Test tokenizing a simple LIST query."""
        lexer = DataviewLexer("LIST")
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.LIST

    def test_tokenize_simple_table(self):
        """Test tokenizing a simple TABLE query."""
        lexer = DataviewLexer("TABLE")
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.TABLE

    def test_tokenize_keywords_case_insensitive(self):
        """Test that keywords are case-insensitive."""
        for keyword in ["TASK", "task", "Task", "tAsK"]:
            lexer = DataviewLexer(keyword)
            tokens = lexer.tokenize()
            assert tokens[0].type == TokenType.TASK
            assert tokens[0].value == "TASK"  # Normalized to uppercase


class TestLexerStrings:
    """Test string tokenization."""

    def test_tokenize_double_quoted_string(self):
        """Test double-quoted strings."""
        lexer = DataviewLexer('"hello world"')
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == "hello world"

    def test_tokenize_single_quoted_string(self):
        """Test single-quoted strings."""
        lexer = DataviewLexer("'hello world'")
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == "hello world"

    def test_tokenize_string_with_escape(self):
        """Test strings with escape sequences."""
        lexer = DataviewLexer(r'"hello \"world\""')
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == 'hello "world"'

    def test_tokenize_empty_string(self):
        """Test empty strings."""
        lexer = DataviewLexer('""')
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == ""

    def test_error_on_unterminated_string(self):
        """Test error on unterminated string."""
        lexer = DataviewLexer('"hello')
        with pytest.raises(ValueError, match="Unterminated string"):
            lexer.tokenize()


class TestLexerNumbers:
    """Test number tokenization."""

    def test_tokenize_integer(self):
        """Test integer tokenization."""
        lexer = DataviewLexer("42")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.NUMBER
        assert tokens[0].value == "42"

    def test_tokenize_float(self):
        """Test float tokenization."""
        lexer = DataviewLexer("3.14")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.NUMBER
        assert tokens[0].value == "3.14"

    def test_tokenize_negative_number(self):
        """Test negative numbers."""
        lexer = DataviewLexer("-42")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.NUMBER
        assert tokens[0].value == "-42"

    def test_tokenize_negative_float(self):
        """Test negative floats."""
        lexer = DataviewLexer("-3.14")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.NUMBER
        assert tokens[0].value == "-3.14"


class TestLexerOperators:
    """Test operator tokenization."""

    def test_tokenize_equals(self):
        """Test = operator."""
        lexer = DataviewLexer("=")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.EQUALS
        assert tokens[0].value == "="

    def test_tokenize_not_equals(self):
        """Test != operator."""
        lexer = DataviewLexer("!=")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.NOT_EQUALS
        assert tokens[0].value == "!="

    def test_tokenize_less_than(self):
        """Test < operator."""
        lexer = DataviewLexer("<")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.LESS_THAN

    def test_tokenize_greater_than(self):
        """Test > operator."""
        lexer = DataviewLexer(">")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.GREATER_THAN

    def test_tokenize_less_equal(self):
        """Test <= operator."""
        lexer = DataviewLexer("<=")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.LESS_EQUAL
        assert tokens[0].value == "<="

    def test_tokenize_greater_equal(self):
        """Test >= operator."""
        lexer = DataviewLexer(">=")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.GREATER_EQUAL
        assert tokens[0].value == ">="

    def test_tokenize_and(self):
        """Test AND operator."""
        lexer = DataviewLexer("AND")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.AND

    def test_tokenize_or(self):
        """Test OR operator."""
        lexer = DataviewLexer("OR")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.OR


class TestLexerIdentifiers:
    """Test identifier tokenization."""

    def test_tokenize_simple_identifier(self):
        """Test simple identifier."""
        lexer = DataviewLexer("status")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "status"

    def test_tokenize_field_path(self):
        """Test field path (e.g., file.name)."""
        lexer = DataviewLexer("file.name")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.FIELD_PATH
        assert tokens[0].value == "file.name"

    def test_tokenize_identifier_with_underscore(self):
        """Test identifier with underscore."""
        lexer = DataviewLexer("my_field")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "my_field"

    def test_tokenize_identifier_with_hyphen(self):
        """Test identifier with hyphen."""
        lexer = DataviewLexer("my-field")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "my-field"


class TestLexerPunctuation:
    """Test punctuation tokenization."""

    def test_tokenize_comma(self):
        """Test comma."""
        lexer = DataviewLexer(",")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.COMMA

    def test_tokenize_lparen(self):
        """Test left parenthesis."""
        lexer = DataviewLexer("(")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.LPAREN

    def test_tokenize_rparen(self):
        """Test right parenthesis."""
        lexer = DataviewLexer(")")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.RPAREN

    def test_tokenize_lbracket(self):
        """Test left bracket."""
        lexer = DataviewLexer("[")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.LBRACKET

    def test_tokenize_rbracket(self):
        """Test right bracket."""
        lexer = DataviewLexer("]")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.RBRACKET


class TestLexerBooleans:
    """Test boolean tokenization."""

    def test_tokenize_true(self):
        """Test true literal."""
        lexer = DataviewLexer("true")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.BOOLEAN
        assert tokens[0].value == "true"

    def test_tokenize_false(self):
        """Test false literal."""
        lexer = DataviewLexer("false")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.BOOLEAN
        assert tokens[0].value == "false"

    def test_tokenize_null(self):
        """Test null literal."""
        lexer = DataviewLexer("null")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.NULL


class TestLexerComments:
    """Test comment handling."""

    def test_skip_line_comment(self):
        """Test that line comments are skipped."""
        lexer = DataviewLexer("TASK // this is a comment")
        tokens = lexer.tokenize()
        assert len(tokens) == 2  # TASK + EOF
        assert tokens[0].type == TokenType.TASK


class TestLexerLineTracking:
    """Test line and column tracking."""

    def test_track_line_numbers(self):
        """Test that line numbers are tracked correctly."""
        lexer = DataviewLexer("TASK\nLIST")
        tokens = lexer.tokenize()
        assert tokens[0].line == 1
        assert tokens[1].line == 2  # LIST is on line 2

    def test_track_column_numbers(self):
        """Test that column numbers are tracked correctly."""
        lexer = DataviewLexer("TASK FROM")
        tokens = lexer.tokenize()
        assert tokens[0].column == 1
        assert tokens[1].column == 6


class TestLexerComplexQueries:
    """Test tokenization of complex queries."""

    def test_tokenize_list_with_from(self):
        """Test LIST FROM query."""
        lexer = DataviewLexer('LIST FROM "1. projects"')
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.LIST
        assert tokens[1].type == TokenType.FROM
        assert tokens[2].type == TokenType.STRING
        assert tokens[2].value == "1. projects"

    def test_tokenize_where_clause(self):
        """Test WHERE clause."""
        lexer = DataviewLexer('WHERE status = "active"')
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.WHERE
        assert tokens[1].type == TokenType.IDENTIFIER
        assert tokens[2].type == TokenType.EQUALS
        assert tokens[3].type == TokenType.STRING

    def test_tokenize_table_with_fields(self):
        """Test TABLE with fields."""
        lexer = DataviewLexer("TABLE title, status, priority")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.TABLE
        assert tokens[1].type == TokenType.IDENTIFIER
        assert tokens[2].type == TokenType.COMMA
        assert tokens[3].type == TokenType.IDENTIFIER
        assert tokens[4].type == TokenType.COMMA
        assert tokens[5].type == TokenType.IDENTIFIER

    def test_tokenize_function_call(self):
        """Test function call."""
        lexer = DataviewLexer('contains(tags, "bug")')
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER  # contains
        assert tokens[1].type == TokenType.LPAREN
        assert tokens[2].type == TokenType.IDENTIFIER  # tags
        assert tokens[3].type == TokenType.COMMA
        assert tokens[4].type == TokenType.STRING  # "bug"
        assert tokens[5].type == TokenType.RPAREN


class TestLexerErrors:
    """Test error handling."""

    def test_error_on_invalid_character(self):
        """Test error on invalid character."""
        lexer = DataviewLexer("TASK @")
        with pytest.raises(ValueError, match="Unexpected character"):
            lexer.tokenize()

    def test_error_on_unterminated_string(self):
        """Test error on unterminated string."""
        lexer = DataviewLexer('"unterminated')
        with pytest.raises(ValueError, match="Unterminated string"):
            lexer.tokenize()


class TestLexerWhitespace:
    """Test whitespace handling."""

    def test_skip_spaces(self):
        """Test that spaces are skipped."""
        lexer = DataviewLexer("TASK    FROM")
        tokens = lexer.tokenize()
        assert len(tokens) == 3  # TASK, FROM, EOF

    def test_skip_tabs(self):
        """Test that tabs are skipped."""
        lexer = DataviewLexer("TASK\t\tFROM")
        tokens = lexer.tokenize()
        assert len(tokens) == 3

    def test_skip_newlines(self):
        """Test that newlines are skipped."""
        lexer = DataviewLexer("TASK\n\nFROM")
        tokens = lexer.tokenize()
        assert len(tokens) == 3
