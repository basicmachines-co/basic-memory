"""
Lexical analyzer (tokenizer) for Dataview queries.
"""

import re
from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    """Token types for Dataview queries."""

    # Keywords
    TABLE = auto()
    LIST = auto()
    TASK = auto()
    CALENDAR = auto()
    FROM = auto()
    WHERE = auto()
    SORT = auto()
    LIMIT = auto()
    FLATTEN = auto()
    GROUP = auto()
    BY = auto()
    WITHOUT = auto()
    ID = auto()
    AS = auto()

    # Operators
    AND = auto()
    OR = auto()
    NOT = auto()
    EQUALS = auto()  # =
    NOT_EQUALS = auto()  # !=
    LESS_THAN = auto()  # <
    GREATER_THAN = auto()  # >
    LESS_EQUAL = auto()  # <=
    GREATER_EQUAL = auto()  # >=

    # Literals
    STRING = auto()
    NUMBER = auto()
    BOOLEAN = auto()
    NULL = auto()

    # Identifiers and paths
    IDENTIFIER = auto()
    FIELD_PATH = auto()  # e.g., file.name, file.link

    # Punctuation
    COMMA = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    DOT = auto()

    # Special
    NEWLINE = auto()
    EOF = auto()


@dataclass
class Token:
    """A token in the Dataview query."""

    type: TokenType
    value: str
    line: int
    column: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.column})"


class DataviewLexer:
    """Tokenizer for Dataview queries."""

    KEYWORDS = {
        "TABLE": TokenType.TABLE,
        "LIST": TokenType.LIST,
        "TASK": TokenType.TASK,
        "CALENDAR": TokenType.CALENDAR,
        "FROM": TokenType.FROM,
        "WHERE": TokenType.WHERE,
        "SORT": TokenType.SORT,
        "LIMIT": TokenType.LIMIT,
        "FLATTEN": TokenType.FLATTEN,
        "GROUP": TokenType.GROUP,
        "BY": TokenType.BY,
        "WITHOUT": TokenType.WITHOUT,
        "ID": TokenType.ID,
        "AS": TokenType.AS,
        "AND": TokenType.AND,
        "OR": TokenType.OR,
        "NOT": TokenType.NOT,
        "TRUE": TokenType.BOOLEAN,
        "FALSE": TokenType.BOOLEAN,
        "NULL": TokenType.NULL,
    }

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []

    def tokenize(self) -> list[Token]:
        """Tokenize the entire input."""
        while self.pos < len(self.text):
            self._skip_whitespace()
            if self.pos >= len(self.text):
                break

            # Try to match a token
            if not self._try_tokenize_one():
                raise ValueError(
                    f"Unexpected character '{self.text[self.pos]}' at {self.line}:{self.column}"
                )

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.column))
        return self.tokens

    def _try_tokenize_one(self) -> bool:
        """Try to tokenize one token. Returns True if successful."""
        # Comments
        if self._match_comment():
            return True

        # Strings
        if self._match_string():
            return True

        # Numbers
        if self._match_number():
            return True

        # Operators (must come before identifiers to match !=, <=, >=)
        if self._match_operator():
            return True

        # Identifiers and keywords
        if self._match_identifier():
            return True

        # Punctuation
        if self._match_punctuation():
            return True

        return False

    def _skip_whitespace(self):
        """Skip whitespace but track newlines."""
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            if self.text[self.pos] == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.pos += 1

    def _match_comment(self) -> bool:
        """Match comments (// or /* */)."""
        if self.pos + 1 < len(self.text) and self.text[self.pos : self.pos + 2] == "//":
            # Line comment
            while self.pos < len(self.text) and self.text[self.pos] != "\n":
                self.pos += 1
            return True
        return False

    def _match_string(self) -> bool:
        """Match string literals."""
        if self.text[self.pos] not in ('"', "'"):
            return False

        quote = self.text[self.pos]
        start_pos = self.pos
        start_col = self.column
        self.pos += 1
        self.column += 1

        value = ""
        while self.pos < len(self.text) and self.text[self.pos] != quote:
            if self.text[self.pos] == "\\":
                # Escape sequence
                self.pos += 1
                self.column += 1
                if self.pos < len(self.text):
                    value += self.text[self.pos]
                    self.pos += 1
                    self.column += 1
            else:
                value += self.text[self.pos]
                self.pos += 1
                self.column += 1

        if self.pos >= len(self.text):
            raise ValueError(f"Unterminated string at {self.line}:{start_col}")

        self.pos += 1  # Skip closing quote
        self.column += 1

        self.tokens.append(Token(TokenType.STRING, value, self.line, start_col))
        return True

    def _match_number(self) -> bool:
        """Match numeric literals."""
        if not self.text[self.pos].isdigit() and self.text[self.pos] != "-":
            return False

        start_col = self.column
        value = ""

        # Optional negative sign
        if self.text[self.pos] == "-":
            value += "-"
            self.pos += 1
            self.column += 1

        # Digits
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            value += self.text[self.pos]
            self.pos += 1
            self.column += 1

        # Optional decimal part
        if self.pos < len(self.text) and self.text[self.pos] == ".":
            value += "."
            self.pos += 1
            self.column += 1
            while self.pos < len(self.text) and self.text[self.pos].isdigit():
                value += self.text[self.pos]
                self.pos += 1
                self.column += 1

        if value and value != "-":
            self.tokens.append(Token(TokenType.NUMBER, value, self.line, start_col))
            return True

        return False

    def _match_operator(self) -> bool:
        """Match operators."""
        start_col = self.column

        # Two-character operators
        if self.pos + 1 < len(self.text):
            two_char = self.text[self.pos : self.pos + 2]
            token_type = None
            if two_char == "!=":
                token_type = TokenType.NOT_EQUALS
            elif two_char == "<=":
                token_type = TokenType.LESS_EQUAL
            elif two_char == ">=":
                token_type = TokenType.GREATER_EQUAL

            if token_type:
                self.tokens.append(Token(token_type, two_char, self.line, start_col))
                self.pos += 2
                self.column += 2
                return True

        # Single-character operators
        char = self.text[self.pos]
        token_type = None
        if char == "=":
            token_type = TokenType.EQUALS
        elif char == "<":
            token_type = TokenType.LESS_THAN
        elif char == ">":
            token_type = TokenType.GREATER_THAN

        if token_type:
            self.tokens.append(Token(token_type, char, self.line, start_col))
            self.pos += 1
            self.column += 1
            return True

        return False

    def _match_identifier(self) -> bool:
        """Match identifiers and keywords."""
        if not (self.text[self.pos].isalpha() or self.text[self.pos] in ("_", "#")):
            return False

        start_col = self.column
        value = ""

        # Match identifier with dots (for field paths like file.name) and tags (#tag)
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char.isalnum() or char in ("_", ".", "-", "#"):
                value += char
                self.pos += 1
                self.column += 1
            else:
                break

        # Check if it's a keyword
        token_type = self.KEYWORDS.get(value.upper())
        if token_type:
            # Preserve case for boolean values
            if token_type == TokenType.BOOLEAN:
                self.tokens.append(Token(token_type, value, self.line, start_col))
            else:
                self.tokens.append(Token(token_type, value.upper(), self.line, start_col))
        else:
            # It's an identifier or field path
            if "." in value:
                token_type = TokenType.FIELD_PATH
            else:
                token_type = TokenType.IDENTIFIER
            self.tokens.append(Token(token_type, value, self.line, start_col))

        return True

    def _match_punctuation(self) -> bool:
        """Match punctuation."""
        char = self.text[self.pos]
        start_col = self.column

        token_type = None
        if char == ",":
            token_type = TokenType.COMMA
        elif char == "(":
            token_type = TokenType.LPAREN
        elif char == ")":
            token_type = TokenType.RPAREN
        elif char == "[":
            token_type = TokenType.LBRACKET
        elif char == "]":
            token_type = TokenType.RBRACKET
        elif char == ".":
            token_type = TokenType.DOT

        if token_type:
            self.tokens.append(Token(token_type, char, self.line, start_col))
            self.pos += 1
            self.column += 1
            return True

        return False
