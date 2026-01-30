"""
Parser for Dataview queries.

Converts tokens into an Abstract Syntax Tree (AST).
"""

from basic_memory.dataview.ast import (
    BinaryOpNode,
    DataviewQuery,
    ExpressionNode,
    FieldNode,
    FunctionCallNode,
    LiteralNode,
    QueryType,
    SortClause,
    SortDirection,
    TableField,
    WhereClause,
)
from basic_memory.dataview.errors import DataviewParseError, DataviewSyntaxError
from basic_memory.dataview.lexer import DataviewLexer, Token, TokenType


class DataviewParser:
    """Parser for Dataview queries."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    @classmethod
    def parse(cls, query_text: str) -> DataviewQuery:
        """Parse a Dataview query string into an AST."""
        lexer = DataviewLexer(query_text)
        tokens = lexer.tokenize()
        parser = cls(tokens)
        return parser.parse_query()

    def parse_query(self) -> DataviewQuery:
        """Parse the complete query."""
        # Parse query type
        query_type = self._parse_query_type()

        # Parse fields (for TABLE queries)
        fields = None
        if query_type == QueryType.TABLE:
            fields = self._parse_table_fields()

        # Parse FROM clause
        from_source = None
        if self._check(TokenType.FROM):
            self._advance()
            from_source = self._parse_from_source()

        # Parse WHERE clause
        where_clause = None
        if self._check(TokenType.WHERE):
            self._advance()
            where_clause = WhereClause(expression=self._parse_expression())

        # Parse SORT clause
        sort_clauses = None
        if self._check(TokenType.SORT):
            self._advance()
            sort_clauses = self._parse_sort_clauses()

        # Parse LIMIT clause
        limit = None
        if self._check(TokenType.LIMIT):
            self._advance()
            limit = self._parse_limit()

        return DataviewQuery(
            query_type=query_type,
            fields=fields,
            from_source=from_source,
            where_clause=where_clause,
            sort_clauses=sort_clauses,
            limit=limit,
        )

    def _parse_query_type(self) -> QueryType:
        """Parse the query type (TABLE, LIST, TASK, CALENDAR)."""
        if self._check(TokenType.TABLE):
            self._advance()
            return QueryType.TABLE
        elif self._check(TokenType.LIST):
            self._advance()
            return QueryType.LIST
        elif self._check(TokenType.TASK):
            self._advance()
            return QueryType.TASK
        elif self._check(TokenType.CALENDAR):
            self._advance()
            return QueryType.CALENDAR
        else:
            raise DataviewSyntaxError(
                f"Expected query type (TABLE, LIST, TASK, CALENDAR), got {self._current().value}",
                self._current().line,
                self._current().column,
            )

    def _parse_table_fields(self) -> list[TableField]:
        """Parse TABLE fields."""
        fields = []

        # Check for WITHOUT ID
        without_id = False
        if self._check(TokenType.WITHOUT):
            self._advance()
            if self._check(TokenType.ID):
                self._advance()
                without_id = True
            else:
                raise DataviewSyntaxError(
                    "Expected ID after WITHOUT",
                    self._current().line,
                    self._current().column,
                )

        # Parse field list
        while not self._check(TokenType.FROM) and not self._is_at_end():
            expr = self._parse_expression()

            # Check for AS alias
            alias = None
            if self._check(TokenType.AS):
                self._advance()
                if self._check(TokenType.IDENTIFIER) or self._check(TokenType.STRING):
                    alias = self._current().value
                    self._advance()
                else:
                    raise DataviewSyntaxError(
                        "Expected alias after AS",
                        self._current().line,
                        self._current().column,
                    )

            fields.append(TableField(expression=expr, alias=alias))

            # Check for comma
            if self._check(TokenType.COMMA):
                self._advance()
            elif not self._check(TokenType.FROM) and not self._is_at_end():
                break

        return fields

    def _parse_from_source(self) -> str:
        """Parse FROM source."""
        if self._check(TokenType.STRING):
            source = self._current().value
            self._advance()
            return source
        elif self._check(TokenType.IDENTIFIER):
            source = self._current().value
            self._advance()
            return source
        else:
            raise DataviewSyntaxError(
                f"Expected source path, got {self._current().value}",
                self._current().line,
                self._current().column,
            )

    def _parse_expression(self) -> ExpressionNode:
        """Parse an expression (handles operator precedence)."""
        return self._parse_or_expression()

    def _parse_or_expression(self) -> ExpressionNode:
        """Parse OR expression."""
        left = self._parse_and_expression()

        while self._check(TokenType.OR):
            op_token = self._current()
            self._advance()
            right = self._parse_and_expression()
            left = BinaryOpNode(operator=op_token.value, left=left, right=right)

        return left

    def _parse_and_expression(self) -> ExpressionNode:
        """Parse AND expression."""
        left = self._parse_comparison_expression()

        while self._check(TokenType.AND):
            op_token = self._current()
            self._advance()
            right = self._parse_comparison_expression()
            left = BinaryOpNode(operator=op_token.value, left=left, right=right)

        return left

    def _parse_comparison_expression(self) -> ExpressionNode:
        """Parse comparison expression."""
        left = self._parse_primary_expression()

        if self._check_any(
            [
                TokenType.EQUALS,
                TokenType.NOT_EQUALS,
                TokenType.LESS_THAN,
                TokenType.GREATER_THAN,
                TokenType.LESS_EQUAL,
                TokenType.GREATER_EQUAL,
            ]
        ):
            op_token = self._current()
            self._advance()
            right = self._parse_primary_expression()
            return BinaryOpNode(operator=op_token.value, left=left, right=right)

        return left

    def _parse_primary_expression(self) -> ExpressionNode:
        """Parse primary expression (literals, fields, function calls)."""
        # String literal
        if self._check(TokenType.STRING):
            value = self._current().value
            self._advance()
            return LiteralNode(value=value)

        # Number literal
        if self._check(TokenType.NUMBER):
            value = self._current().value
            self._advance()
            # Convert to int or float
            if "." in value:
                return LiteralNode(value=float(value))
            else:
                return LiteralNode(value=int(value))

        # Boolean literal
        if self._check(TokenType.BOOLEAN):
            value = self._current().value.lower() == "true"
            self._advance()
            return LiteralNode(value=value)

        # Null literal
        if self._check(TokenType.NULL):
            self._advance()
            return LiteralNode(value=None)

        # Field path (e.g., file.name)
        if self._check(TokenType.FIELD_PATH):
            field_name = self._current().value
            self._advance()
            return FieldNode(field_name=field_name)

        # Identifier (could be field or function call)
        if self._check(TokenType.IDENTIFIER):
            name = self._current().value
            self._advance()

            # Check if it's a function call
            if self._check(TokenType.LPAREN):
                self._advance()
                args = self._parse_function_arguments()
                if not self._check(TokenType.RPAREN):
                    raise DataviewSyntaxError(
                        "Expected ')' after function arguments",
                        self._current().line,
                        self._current().column,
                    )
                self._advance()
                return FunctionCallNode(function_name=name, arguments=args)
            else:
                # It's a field reference
                return FieldNode(field_name=name)

        # Parenthesized expression
        if self._check(TokenType.LPAREN):
            self._advance()
            expr = self._parse_expression()
            if not self._check(TokenType.RPAREN):
                raise DataviewSyntaxError(
                    "Expected ')' after expression",
                    self._current().line,
                    self._current().column,
                )
            self._advance()
            return expr

        raise DataviewSyntaxError(
            f"Unexpected token: {self._current().value}",
            self._current().line,
            self._current().column,
        )

    def _parse_function_arguments(self) -> list[ExpressionNode]:
        """Parse function arguments."""
        args = []

        if self._check(TokenType.RPAREN):
            return args

        args.append(self._parse_expression())

        while self._check(TokenType.COMMA):
            self._advance()
            args.append(self._parse_expression())

        return args

    def _parse_sort_clauses(self) -> list[SortClause]:
        """Parse SORT clauses."""
        clauses = []

        while True:
            if not self._check(TokenType.IDENTIFIER) and not self._check(TokenType.FIELD_PATH):
                break

            field = self._current().value
            self._advance()

            # Check for direction
            direction = SortDirection.ASC
            if self._check(TokenType.IDENTIFIER):
                dir_str = self._current().value.upper()
                if dir_str in ("ASC", "DESC"):
                    direction = SortDirection.ASC if dir_str == "ASC" else SortDirection.DESC
                    self._advance()

            clauses.append(SortClause(field=field, direction=direction))

            # Check for comma
            if self._check(TokenType.COMMA):
                self._advance()
            else:
                break

        return clauses

    def _parse_limit(self) -> int:
        """Parse LIMIT value."""
        if not self._check(TokenType.NUMBER):
            raise DataviewSyntaxError(
                f"Expected number after LIMIT, got {self._current().value}",
                self._current().line,
                self._current().column,
            )

        limit = int(self._current().value)
        self._advance()
        return limit

    # Helper methods

    def _current(self) -> Token:
        """Get the current token."""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.tokens[-1]  # Return EOF

    def _advance(self) -> Token:
        """Advance to the next token."""
        token = self._current()
        if not self._is_at_end():
            self.pos += 1
        return token

    def _check(self, token_type: TokenType) -> bool:
        """Check if current token matches the given type."""
        if self._is_at_end():
            return False
        return self._current().type == token_type

    def _check_any(self, token_types: list[TokenType]) -> bool:
        """Check if current token matches any of the given types."""
        return any(self._check(t) for t in token_types)

    def _is_at_end(self) -> bool:
        """Check if we're at the end of tokens."""
        return self._current().type == TokenType.EOF
