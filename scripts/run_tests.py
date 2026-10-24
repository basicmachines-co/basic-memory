"""Run the justfile test suites, optionally sharding before testmon selects tests.

Every job computes the same partition from repository files and committed timings.
Splitting pytest's collected items instead is unsafe with independent testmon caches:
each job can collect a different subset before pytest-split assigns ownership.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def split_files(files: list[Path], durations: dict[str, float], groups: int) -> list[list[Path]]:
    """Greedily balance whole files, preserving disjoint, exhaustive ownership."""
    if groups < 1:
        raise ValueError("groups must be positive")

    file_durations: dict[Path, float] = {}
    for nodeid, duration in durations.items():
        path = Path(nodeid.split("::", 1)[0])
        file_durations[path] = file_durations.get(path, 0.0) + duration

    known = [file_durations[path] for path in files if path in file_durations]
    # New files have no history. Use the suite's mean file time; a wholly new
    # suite uses file size until its first timing recording is available.
    mean_duration = sum(known) / len(known) if known else 0.0
    weights = {
        path: file_durations.get(path, mean_duration) if known else float(path.stat().st_size)
        for path in files
    }
    shards: list[list[Path]] = [[] for _ in range(groups)]
    totals = [0.0] * groups
    for path in sorted(files, key=lambda path: (-weights[path], path.as_posix())):
        group = min(range(groups), key=lambda index: (totals[index], len(shards[index]), index))
        shards[group].append(path)
        totals[group] += weights[path]
    return [sorted(shard) for shard in shards]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--splits", "--groups", dest="groups", type=int, default=1)
    parser.add_argument("--group", type=int, default=1, help="One-based shard number")
    parser.add_argument("--timings", type=Path, default=Path(".test_durations"))
    parser.add_argument("--list-files", action="store_true")
    parser.add_argument("--file-list", type=Path, help="Save the assigned files for CI diagnostics")
    args, pytest_args = parser.parse_known_args()
    if "--" in pytest_args:
        pytest_args.remove("--")
    if not 1 <= args.group <= args.groups:
        parser.error("group must be between 1 and groups")
    files = sorted(args.root.rglob("test_*.py"))
    if not files:
        parser.error(f"no test files found under {args.root}")
    durations = json.loads(args.timings.read_text()) if args.timings.exists() else {}
    selected = split_files(files, durations, args.groups)[args.group - 1]
    file_list = "".join(f"{path.as_posix()}\n" for path in selected)
    if args.file_list:
        args.file_list.write_text(file_list)
    if args.list_files:
        print(file_list, end="")
        return 0
    # An empty partition is valid; never invoke pytest with no paths, which
    # would accidentally discover and run the entire suite.
    if not selected:
        print(f"Shard {args.group}/{args.groups} has no test files")
        return 0

    postgres = os.environ.get("BASIC_MEMORY_TEST_POSTGRES", "").lower() in ("1", "true", "yes")
    backend = "postgres" if postgres else "sqlite"
    environment = f"{backend}-{args.root.name}-{args.group}of{args.groups}-ctrace-v1"
    # Python 3.14 defaults to sysmon, which misses repeated per-test contexts
    # with the current testmon collector. C tracing records every dependency.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"--testmon-env={environment}",
            *pytest_args,
            *map(str, selected),
        ],
        env={**os.environ, "COVERAGE_CORE": "ctrace"},
        check=False,
    )
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
