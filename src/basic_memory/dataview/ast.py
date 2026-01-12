"""
Abstract Syntax Tree (AST) definitions for Dataview queries.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class QueryType(Enum):
    """Type of Dataview query."""

    TABLE = "TABLE"
    LIST = "LIST"
    TASK = "TASK"
    CALENDAR = "CALENDAR"


class SortDirection(Enum):
    """Sort direction for SORT clause."""

    ASC = "ASC"
    DESC = "DESC"


@dataclass
class ExpressionNode:
    """Base class for expression nodes in the AST."""

    pass


@dataclass
class LiteralNode(ExpressionNode):
    """Literal value (string, number, boolean, null)."""

    value: Any


@dataclass
class FieldNode(ExpressionNode):
    """Field reference (e.g., 'status', 'file.name')."""

    field_name: str


@dataclass
class BinaryOpNode(ExpressionNode):
    """Binary operation (e.g., 'status = "active"', 'priority > 1')."""

    operator: str  # =, !=, <, >, <=, >=, AND, OR
    left: ExpressionNode
    right: ExpressionNode


@dataclass
class FunctionCallNode(ExpressionNode):
    """Function call (e.g., 'contains(tags, "bug")')."""

    function_name: str
    arguments: list[ExpressionNode]


@dataclass
class TableField:
    """Field specification in TABLE query."""

    expression: ExpressionNode
    alias: str | None = None


@dataclass
class WhereClause:
    """WHERE clause filtering."""

    expression: ExpressionNode


@dataclass
class SortClause:
    """SORT clause ordering."""

    field: str
    direction: SortDirection = SortDirection.ASC


@dataclass
class DataviewQuery:
    """Complete Dataview query AST."""

    query_type: QueryType
    fields: list[TableField] | None = None  # For TABLE queries
    from_source: str | None = None  # FROM clause
    where_clause: WhereClause | None = None  # WHERE clause
    sort_clauses: list[SortClause] | None = None  # SORT clause
    limit: int | None = None  # LIMIT clause
    flatten: bool = False  # FLATTEN modifier
    group_by: str | None = None  # GROUP BY clause

    def __repr__(self) -> str:
        parts = [f"DataviewQuery(type={self.query_type.value}"]
        if self.fields:
            parts.append(f"fields={len(self.fields)}")
        if self.from_source:
            parts.append(f"from={self.from_source!r}")
        if self.where_clause:
            parts.append("where=...")
        if self.sort_clauses:
            parts.append(f"sort={len(self.sort_clauses)}")
        if self.limit:
            parts.append(f"limit={self.limit}")
        return ", ".join(parts) + ")"
