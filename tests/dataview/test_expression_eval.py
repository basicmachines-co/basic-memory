"""Tests for ExpressionEvaluator."""

import pytest

from basic_memory.dataview.ast import (
    BinaryOpNode,
    FieldNode,
    FunctionCallNode,
    LiteralNode,
)
from basic_memory.dataview.errors import DataviewExecutionError
from basic_memory.dataview.executor.expression_eval import ExpressionEvaluator


class TestExpressionEvaluatorLiterals:
    """Test evaluating literal expressions."""

    def test_evaluate_string_literal(self, note_with_frontmatter):
        """Test evaluating string literal."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = LiteralNode(value="hello")
        result = evaluator.evaluate(expr)
        assert result == "hello"

    def test_evaluate_number_literal(self, note_with_frontmatter):
        """Test evaluating number literal."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = LiteralNode(value=42)
        result = evaluator.evaluate(expr)
        assert result == 42

    def test_evaluate_float_literal(self, note_with_frontmatter):
        """Test evaluating float literal."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = LiteralNode(value=3.14)
        result = evaluator.evaluate(expr)
        assert result == 3.14

    def test_evaluate_boolean_true(self, note_with_frontmatter):
        """Test evaluating true literal."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = LiteralNode(value=True)
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_boolean_false(self, note_with_frontmatter):
        """Test evaluating false literal."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = LiteralNode(value=False)
        result = evaluator.evaluate(expr)
        assert result is False

    def test_evaluate_null_literal(self, note_with_frontmatter):
        """Test evaluating null literal."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = LiteralNode(value=None)
        result = evaluator.evaluate(expr)
        assert result is None


class TestExpressionEvaluatorFields:
    """Test evaluating field expressions."""

    def test_evaluate_field_reference(self, note_with_frontmatter):
        """Test evaluating field reference."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FieldNode(field_name="status")
        result = evaluator.evaluate(expr)
        assert result == "active"

    def test_evaluate_field_path(self, note_with_frontmatter):
        """Test evaluating field path."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FieldNode(field_name="file.name")
        result = evaluator.evaluate(expr)
        assert result == "Test Note"

    def test_evaluate_missing_field(self, note_with_frontmatter):
        """Test evaluating missing field."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FieldNode(field_name="nonexistent")
        result = evaluator.evaluate(expr)
        assert result is None


class TestExpressionEvaluatorBinaryOps:
    """Test evaluating binary operations."""

    def test_evaluate_equals(self, note_with_frontmatter):
        """Test equals operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="=",
            left=FieldNode(field_name="status"),
            right=LiteralNode(value="active"),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_not_equals(self, note_with_frontmatter):
        """Test not equals operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="!=",
            left=FieldNode(field_name="status"),
            right=LiteralNode(value="archived"),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_less_than(self, note_with_frontmatter):
        """Test less than operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="<",
            left=FieldNode(field_name="priority"),
            right=LiteralNode(value=5),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_greater_than(self, note_with_frontmatter):
        """Test greater than operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator=">",
            left=FieldNode(field_name="priority"),
            right=LiteralNode(value=0),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_less_equal(self, note_with_frontmatter):
        """Test less or equal operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="<=",
            left=FieldNode(field_name="priority"),
            right=LiteralNode(value=1),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_greater_equal(self, note_with_frontmatter):
        """Test greater or equal operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator=">=",
            left=FieldNode(field_name="priority"),
            right=LiteralNode(value=1),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_and(self, note_with_frontmatter):
        """Test AND operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="AND",
            left=BinaryOpNode(
                operator="=",
                left=FieldNode(field_name="status"),
                right=LiteralNode(value="active"),
            ),
            right=BinaryOpNode(
                operator=">",
                left=FieldNode(field_name="priority"),
                right=LiteralNode(value=0),
            ),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_or(self, note_with_frontmatter):
        """Test OR operator."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="OR",
            left=BinaryOpNode(
                operator="=",
                left=FieldNode(field_name="status"),
                right=LiteralNode(value="archived"),
            ),
            right=BinaryOpNode(
                operator="=",
                left=FieldNode(field_name="status"),
                right=LiteralNode(value="active"),
            ),
        )
        result = evaluator.evaluate(expr)
        assert result is True


class TestExpressionEvaluatorFunctions:
    """Test evaluating function calls."""

    def test_evaluate_contains_list(self, note_with_frontmatter):
        """Test contains() with list."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="contains",
            arguments=[
                FieldNode(field_name="tags"),
                LiteralNode(value="test"),
            ],
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_contains_string(self, note_with_frontmatter):
        """Test contains() with string."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="contains",
            arguments=[
                FieldNode(field_name="status"),
                LiteralNode(value="act"),
            ],
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_length_list(self, note_with_frontmatter):
        """Test length() with list."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="length",
            arguments=[FieldNode(field_name="tags")],
        )
        result = evaluator.evaluate(expr)
        assert result == 2

    def test_evaluate_length_string(self, note_with_frontmatter):
        """Test length() with string."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="length",
            arguments=[FieldNode(field_name="status")],
        )
        result = evaluator.evaluate(expr)
        assert result == 6  # "active"

    def test_evaluate_lower(self, note_with_frontmatter):
        """Test lower() function."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="lower",
            arguments=[LiteralNode(value="HELLO")],
        )
        result = evaluator.evaluate(expr)
        assert result == "hello"

    def test_evaluate_upper(self, note_with_frontmatter):
        """Test upper() function."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="upper",
            arguments=[LiteralNode(value="hello")],
        )
        result = evaluator.evaluate(expr)
        assert result == "HELLO"


class TestExpressionEvaluatorErrors:
    """Test error handling."""

    def test_error_on_unknown_function(self, note_with_frontmatter):
        """Test error on unknown function."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="unknown",
            arguments=[],
        )
        with pytest.raises(DataviewExecutionError, match="Unknown function"):
            evaluator.evaluate(expr)

    def test_error_on_wrong_arg_count(self, note_with_frontmatter):
        """Test error on wrong argument count."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="contains",
            arguments=[LiteralNode(value="test")],  # Needs 2 args
        )
        with pytest.raises(DataviewExecutionError, match="requires 2 arguments"):
            evaluator.evaluate(expr)


class TestExpressionEvaluatorEdgeCases:
    """Test edge cases."""

    def test_evaluate_comparison_with_none(self, note_with_frontmatter):
        """Test comparison with None values."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="<",
            left=FieldNode(field_name="nonexistent"),
            right=LiteralNode(value=5),
        )
        result = evaluator.evaluate(expr)
        assert result is False

    def test_evaluate_and_with_false(self, note_with_frontmatter):
        """Test AND with false value."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="AND",
            left=LiteralNode(value=False),
            right=LiteralNode(value=True),
        )
        result = evaluator.evaluate(expr)
        assert result is False

    def test_evaluate_or_with_true(self, note_with_frontmatter):
        """Test OR with true value."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = BinaryOpNode(
            operator="OR",
            left=LiteralNode(value=True),
            right=LiteralNode(value=False),
        )
        result = evaluator.evaluate(expr)
        assert result is True

    def test_evaluate_contains_not_found(self, note_with_frontmatter):
        """Test contains() when value not found."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="contains",
            arguments=[
                FieldNode(field_name="tags"),
                LiteralNode(value="notfound"),
            ],
        )
        result = evaluator.evaluate(expr)
        assert result is False

    def test_evaluate_length_none(self, note_with_frontmatter):
        """Test length() with None value."""
        evaluator = ExpressionEvaluator(note_with_frontmatter)
        expr = FunctionCallNode(
            function_name="length",
            arguments=[FieldNode(field_name="nonexistent")],
        )
        result = evaluator.evaluate(expr)
        assert result == 0
