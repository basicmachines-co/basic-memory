"""
Custom exceptions for Dataview parsing and execution.
"""


class DataviewError(Exception):
    """Base exception for all Dataview-related errors."""

    pass


class DataviewSyntaxError(DataviewError):
    """Raised when a Dataview query has invalid syntax."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        self.line = line
        self.column = column
        location = ""
        if line is not None:
            location = f" at line {line}"
            if column is not None:
                location += f", column {column}"
        super().__init__(f"{message}{location}")


class DataviewParseError(DataviewError):
    """Raised when parsing fails."""

    pass


class DataviewExecutionError(DataviewError):
    """Raised when query execution fails."""

    pass
