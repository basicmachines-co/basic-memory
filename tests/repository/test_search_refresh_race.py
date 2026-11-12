"""Only the two ways a search refresh loses to a concurrent refresh count as a lost race."""

import subprocess
import sys

import pytest
from asyncpg.exceptions import DeadlockDetectedError, UniqueViolationError
from sqlalchemy.exc import DBAPIError, IntegrityError

from basic_memory.repository.search_refresh_race import lost_search_refresh_race


def _adapted(driver_error: BaseException) -> DBAPIError:
    """Mirror the asyncpg dialect: its DBAPI error is raised from the driver's error."""
    adapted = Exception(str(driver_error))
    adapted.__cause__ = driver_error
    return IntegrityError("INSERT INTO search_index ...", {}, adapted)


def _unique_violation(constraint_name: str) -> UniqueViolationError:
    # The driver builds its errors from the server's error fields; "n" is the constraint.
    return UniqueViolationError.new(
        {"C": "23505", "M": "duplicate key value violates unique constraint", "n": constraint_name}
    )


def test_a_deadlock_is_a_lost_race() -> None:
    assert lost_search_refresh_race(_adapted(DeadlockDetectedError("deadlock detected"))) == (
        "deadlock"
    )


def test_a_search_index_primary_key_conflict_is_a_lost_race() -> None:
    error = _adapted(_unique_violation("search_index_pkey"))
    assert lost_search_refresh_race(error) == "search_index_pkey"


@pytest.mark.parametrize(
    "error",
    [
        _adapted(_unique_violation("uix_relation_from_id_to_name")),
        _adapted(ValueError("not a driver error")),
        RuntimeError("not a database error"),
    ],
)
def test_every_other_failure_is_not_a_lost_race(error: BaseException) -> None:
    assert lost_search_refresh_race(error) is None


def test_sqlite_startup_never_imports_the_postgres_driver() -> None:
    """A SQLite install must start even when asyncpg's native extension cannot load (#2103)."""
    probe = (
        "import sys\n"
        "import basic_memory.cli.main\n"
        "import basic_memory.mcp.server\n"
        "assert 'asyncpg' not in sys.modules, 'asyncpg imported at startup'\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
