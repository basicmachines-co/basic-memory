"""The redundancy picker never removes coverage, even between mutual duplicates."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "test_redundancy_script", ROOT / "scripts" / "test_redundancy.py"
)
assert SPEC is not None and SPEC.loader is not None
redundancy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(redundancy)


def _file(path: str, lines: set[int], seconds: float):
    return redundancy.TestFile(
        path, tests=1, lines=frozenset(("src.py", line) for line in lines), seconds=seconds
    )


def test_mutual_duplicates_keep_one_and_unique_files_stay() -> None:
    slow_copy = _file("test-int/mcp/test_slow.py", {1, 2}, seconds=9.0)
    fast_copy = _file("tests/mcp/test_fast.py", {1, 2}, seconds=1.0)
    unique = _file("tests/mcp/test_unique.py", {2, 3}, seconds=5.0)

    removed = redundancy.removable([slow_copy, fast_copy, unique], only=None)

    # The slower duplicate goes first; the faster one then owns lines 1-2 alone.
    assert [f.path for f in removed] == ["test-int/mcp/test_slow.py"]


def test_only_limits_candidates_but_counts_every_file() -> None:
    integration = _file("test-int/mcp/test_a.py", {1}, seconds=1.0)
    unit = _file("tests/mcp/test_b.py", {1}, seconds=9.0)

    removed = redundancy.removable([integration, unit], only="test-int/mcp")

    assert [f.path for f in removed] == ["test-int/mcp/test_a.py"]
