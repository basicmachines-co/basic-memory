"""Count the rows and bytes the database hands back to Basic Memory.

Cloud pays for every byte Postgres sends to the application, and search was most of
it (#1746). A test wraps an operation in ``record_traffic`` and asserts on what came
back over the wire, so a change that starts shipping note text again fails a test
instead of showing up on a bill.

Both async drivers SQLAlchemy uses here (asyncpg, aiosqlite) read a statement's whole
result into the adapted cursor's ``_rows`` buffer before ``after_cursor_execute``
fires, so the listener can size the result without consuming it.
"""

import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

# The width of a fixed-size value on the wire: an int8, a float8, a timestamp.
FIXED_VALUE_BYTES = 8


def value_bytes(value: Any) -> int:
    """Approximate the wire size of one column value."""
    match value:
        case None:
            return 0
        case str():
            return len(value.encode("utf-8"))
        case bytes() | bytearray() | memoryview():
            return len(value)
        case bool() | int() | float() | datetime() | date():
            return FIXED_VALUE_BYTES
        case dict() | list():
            return len(json.dumps(value, default=str).encode("utf-8"))
        case _:
            return len(str(value).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class StatementTraffic:
    """One executed statement and the result it returned."""

    sql: str
    rows: int
    bytes: int

    @property
    def label(self) -> str:
        """The statement's SQL on one line, short enough to read in a report."""
        return " ".join(self.sql.split())[:160]


@dataclass(slots=True)
class TrafficLog:
    """Every statement run while recording, in execution order."""

    statements: list[StatementTraffic] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(statement.rows for statement in self.statements)

    @property
    def bytes(self) -> int:
        return sum(statement.bytes for statement in self.statements)

    def largest(self, count: int = 5) -> list[tuple[str, int]]:
        """The statements that returned the most bytes, summed per statement text."""
        totals: Counter[str] = Counter()
        for statement in self.statements:
            totals[statement.label] += statement.bytes
        return totals.most_common(count)


@contextmanager
def record_traffic(engine: AsyncEngine) -> Iterator[TrafficLog]:
    """Record the rows and bytes each statement on ``engine`` returns."""
    log = TrafficLog()

    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        # Read the buffer directly: a driver that stops buffering this way must raise,
        # because a recorder that silently counted zero would pass every assertion.
        rows = list(cursor._rows)
        log.statements.append(
            StatementTraffic(
                sql=statement,
                rows=len(rows),
                bytes=sum(value_bytes(value) for row in rows for value in row),
            )
        )

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "after_cursor_execute", after_cursor_execute)
    try:
        yield log
    finally:
        event.remove(sync_engine, "after_cursor_execute", after_cursor_execute)
