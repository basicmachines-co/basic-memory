"""Find test files that can be deleted without losing any line coverage.

Reads a coverage data file recorded with per-test contexts (--cov-context=test, under
the ctrace core: sysmon credits a line only to the first test that runs it). A file
is removable when every line it covers is still covered by the files that remain.
Removal is greedy, slowest file first, and each removal is checked against what is
left, so two files that only duplicate each other never both go.

Line coverage cannot see assertions. A removable file may still be the only test of
a behavior, an output format, or an error message, so read each one before deleting.
Files whose code runs in a subprocess record almost no lines here and are skipped.

    python scripts/test_redundancy.py .coverage-contexts [--junit results.xml ...]
        [--only test-int/mcp] [--min-lines 20]
"""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from coverage import CoverageData

type LineKey = tuple[str, int]


@dataclass(frozen=True)
class TestFile:
    path: str
    tests: int
    lines: frozenset[LineKey]
    seconds: float


def load_test_files(coverage_file: Path) -> dict[str, tuple[set[str], set[LineKey]]]:
    """Map each test file to its test ids and the source lines its tests ran."""
    data = CoverageData(str(coverage_file))
    data.read()
    by_file: dict[str, tuple[set[str], set[LineKey]]] = defaultdict(lambda: (set(), set()))
    for source in data.measured_files():
        for line, contexts in data.contexts_by_lineno(source).items():
            for context in contexts:
                if not context:
                    continue
                test_id = context.split("|", 1)[0]
                test_ids, lines = by_file[test_id.split("::", 1)[0]]
                test_ids.add(test_id)
                lines.add((source, line))
    return by_file


def junit_seconds(paths: list[Path], known: set[str]) -> Counter[str]:
    """Total recorded seconds per test file. JUnit names modules, not paths."""
    seconds: Counter[str] = Counter()
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            parts = case.get("classname", "").split(".")
            # A classname is module[.Class]; the longest prefix naming a known file wins.
            for cut in range(len(parts), 0, -1):
                candidate = "/".join(parts[:cut]) + ".py"
                if candidate in known:
                    seconds[candidate] += float(case.get("time", 0))
                    break
    return seconds


def removable(files: list[TestFile], only: str | None) -> list[TestFile]:
    """Greedily pick files whose lines stay covered by the files that remain."""
    owners: Counter[LineKey] = Counter()
    for test_file in files:
        owners.update(test_file.lines)

    removed = []
    candidates = [f for f in files if only is None or f.path.startswith(only)]
    for test_file in sorted(candidates, key=lambda f: (-f.seconds, f.path)):
        if all(owners[line] > 1 for line in test_file.lines):
            owners.subtract(test_file.lines)
            removed.append(test_file)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("coverage_file", type=Path)
    parser.add_argument("--junit", type=Path, nargs="*", default=[])
    parser.add_argument("--only", help="Only consider files under this path prefix")
    parser.add_argument(
        "--min-lines",
        type=int,
        default=20,
        help="Skip files covering fewer lines (their code runs in a subprocess)",
    )
    args = parser.parse_args()

    by_file = load_test_files(args.coverage_file)
    seconds = junit_seconds(args.junit, set(by_file))
    files = [
        TestFile(path, len(test_ids), frozenset(lines), seconds[path])
        for path, (test_ids, lines) in by_file.items()
        if len(lines) >= args.min_lines
    ]

    picked = removable(files, args.only)
    total = sum(f.seconds for f in picked)
    print(f"{len(picked)} removable files, {sum(f.tests for f in picked)} tests, {total:.0f}s")
    for test_file in picked:
        print(f"{test_file.seconds:8.1f}s {test_file.tests:4} tests  {test_file.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
