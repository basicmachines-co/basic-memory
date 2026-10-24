# Test performance and incremental runs

CI and local runs use the same root justfile recipes. CI runs complete suites in parallel shards without dependency tracing or caches.
Local incremental selection remains available through `just fast-test`.

## Local iteration

```sh
# Record a full baseline for the collection used by fast-test, then publish it
# to the shared Git cache. This is intentionally a full, potentially slow run.
just testmon-refresh
just fast-test

# Run the same shard as CI, or inspect its assigned files without executing tests.
just test-unit-sqlite --splits 4 --group 1
just test-unit-sqlite --splits 4 --group 1 --list-files
just test-int-postgres --splits 4 --group 1

# Ordinary pytest options are forwarded, including quoted expressions.
just test-unit-sqlite -k 'test_ci_test_selection or test_pr_title_workflow'
just test-int-postgres --durations=30 --junitxml=postgres-results.xml
```

`testmon-refresh` records the same collection/environment as `fast-test`; it does not
also execute the Postgres suite. Use `just test-postgres` for full Postgres validation.
Local `fast-test` retains `COVERAGE_CORE=ctrace` and its separate testmon environment
so repeated per-test dependencies are recorded correctly on Python 3.14.

## CI partitioning

`scripts/run_tests.py` assigns complete files to disjoint groups using committed
`.test_durations`. Files with recorded durations are balanced by their aggregate time;
new files use the suite's mean file time. A suite with no timing history uses file sizes.
`just test-durations-postgres` refreshes the existing unit timing file.
The partition keeps tests sharing a file together and runs every assigned test.

Current parallelism:

| Suite | Shards per runtime |
| --- | ---: |
| SQLite unit | 4 |
| SQLite integration | 2 |
| Postgres unit | 6 |
| Postgres integration | 4 |

Both main and branch builds run complete suites. There are no testmon baselines,
selection flags, or dependency caches in CI. Subprocess tests are included normally.
`just test-cli-smoke` also provides a small, early startup/exit check covering
help/version startup, initialization, schema validation, and reset exit behavior.

Each job publishes its assigned file list and JUnit results and prints its 30 slowest
test phases. Use those results to evaluate fixture costs and duplicate scenarios before
removing coverage. Increased job counts can hit account concurrency limits; compare
queue time and complete workflow wall time, not just individual job duration.

## Initial evidence, 2026-10-24

Implementation base: `2f66021c1e50ce0023dc2c392c1336b3988a1891` (main).
The existing [exact-main run](https://github.com/basicmachines-co/basic-memory/actions/runs/40115550931)
is the CI baseline; it predates these changes.

The earlier [successful branch run](https://github.com/basicmachines-co/basic-memory/actions/runs/40055593080)
took about 28.5 minutes. SQLite unit test steps took 20–27 minutes; the three Postgres
unit shards each took 24–27 minutes. Installation was generally seconds.

Its Postgres integration step was forcibly killed after 600 seconds at only 9% complete,
and the old recipe accepted exit 137 as success. This was not a completed test suite or
a teardown-only delay. The shared runner now propagates failed/killed subprocesses;
the per-test timeout and CI job deadline remain failure boundaries.

A local, deliberately interrupted Postgres integration sample on the implementation
base completed 120 tests in 670.92 seconds. Its slowest observed call was the concurrent
append/refusal test at 53.65 seconds. This sample was not a full-suite pass and is not
directly comparable to CI hardware. The SQLite sample was also interrupted and must
not be reported as a successful full run.

No tests were removed or moved off the PR gate in this change. An after-change CI run
is still required to measure the actual wall-clock improvement and shard imbalance.

The first traced CI trial hit heavy-test timeouts. Locally, the same overlapping-save
test passed in 52.95 seconds without tracing and 136.05 seconds with testmon C tracing.
CI therefore uses sharding alone; local incremental selection remains opt-in.
