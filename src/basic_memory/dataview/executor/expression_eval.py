"""
Expression evaluator for Dataview queries.

Evaluates AST expressions against note data.
"""

from typing import Any

from basic_memory.dataview.ast import (
    BinaryOpNode,
    ExpressionNode,
    FieldNode,
    FunctionCallNode,
    LiteralNode,
)
from basic_memory.dataview.errors import DataviewExecutionError
from basic_memory.dataview.executor.field_resolver import FieldResolver


class ExpressionEvaluator:
    """Evaluates expressions in the context of a note."""

    def __init__(self, note: dict[str, Any]):
        self.note = note
        self.field_resolver = FieldResolver()

    def evaluate(self, expression: ExpressionNode) -> Any:
        """
        Evaluate an expression node.

        Args:
            expression: AST expression node

        Returns:
            Evaluated value
        """
        if isinstance(expression, LiteralNode):
            return expression.value

        elif isinstance(expression, FieldNode):
            if not expression.field_name:
                raise DataviewExecutionError("Field node missing field_name")
            return self.field_resolver.resolve_field(self.note, expression.field_name)

        elif isinstance(expression, BinaryOpNode):
            if not expression.left or not expression.right:
                raise DataviewExecutionError("Binary operation missing operands")
            left = self.evaluate(expression.left)
            right = self.evaluate(expression.right)
            return self._eval_binary_op(expression.operator or "", left, right)

        elif isinstance(expression, FunctionCallNode):
            # Evaluate arguments (can be empty for some functions)
            args = [self.evaluate(arg) for arg in expression.arguments] if expression.arguments else []
            return self._eval_function(expression.function_name, args)

        else:
            raise DataviewExecutionError(f"Unknown expression type: {type(expression)}")

    def _eval_binary_op(self, operator: str, left: Any, right: Any) -> Any:
        """Evaluate binary operations."""
        if operator == "=":
            return left == right
        elif operator == "!=":
            return left != right
        elif operator == "<":
            return left < right if left is not None and right is not None else False
        elif operator == ">":
            return left > right if left is not None and right is not None else False
        elif operator == "<=":
            return left <= right if left is not None and right is not None else False
        elif operator == ">=":
            return left >= right if left is not None and right is not None else False
        elif operator.upper() == "AND":
            return bool(left) and bool(right)
        elif operator.upper() == "OR":
            return bool(left) or bool(right)
        else:
            raise DataviewExecutionError(f"Unknown operator: {operator}")

    def _eval_function(self, function_name: str, args: list[Any]) -> Any:
        """Evaluate function calls."""
        if function_name == "contains":
            if len(args) != 2:
                raise DataviewExecutionError("contains() requires 2 arguments")
            collection, value = args
            if isinstance(collection, list):
                return value in collection
            elif isinstance(collection, str):
                return str(value) in collection
            return False

        elif function_name == "length":
            if len(args) != 1:
                raise DataviewExecutionError("length() requires 1 argument")
            value = args[0]
            if hasattr(value, "__len__"):
                return len(value)
            return 0

        elif function_name == "lower":
            if len(args) != 1:
                raise DataviewExecutionError("lower() requires 1 argument")
            value = args[0]
            if value and hasattr(value, "lower"):
                return value.lower()
            return value

        elif function_name == "upper":
            if len(args) != 1:
                raise DataviewExecutionError("upper() requires 1 argument")
            value = args[0]
            if value and hasattr(value, "upper"):
                return value.upper()
            return value

        else:
            raise DataviewExecutionError(f"Unknown function: {function_name}")
