# Test performance and incremental runs

CI and local runs use the same root justfile recipes. Full runs remain the default;
incremental selection and sharding are explicit options.

## Local iteration

```sh
# Record a full baseline for the collection used by fast-test, then publish it
# to the shared Git cache. This is intentionally a full, potentially slow run.
just testmon-refresh
just fast-test

# Record/select within one backend suite instead.
just test-unit-sqlite --testmon-noselect
just test-unit-sqlite --testmon --testmon-forceselect

# Run the same shard as CI, or inspect its assigned files without executing tests.
just test-unit-sqlite --splits 4 --group 1
just test-unit-sqlite --splits 4 --group 1 --list-files
just test-int-postgres --splits 4 --group 1

# Ordinary pytest options are forwarded, including quoted expressions.
just test-unit-sqlite -k 'test_ci_test_selection or test_pr_title_workflow'
just test-int-postgres --durations=30 --junitxml=postgres-results.xml
```

The suite recipes keep separate testmon environments for the backend, suite, and
shard topology. Recording an unsharded baseline does not populate a sharded baseline.
`testmon-refresh` records the same collection/environment as `fast-test`; it does not
also execute the Postgres suite. Use `just test-postgres` for full Postgres validation.

On Python 3.14, coverage.py's default sysmon tracer misses repeated per-test contexts
with the current testmon collector. The runner and `fast-test` use `COVERAGE_CORE=ctrace`.
New environment names prevent reuse of older dependency recordings. The regression
test runs two independent shards, confirms unchanged tests are skipped, then changes
a shared function and requires every affected test to run and fail.

## CI partitioning and selection

`scripts/run_tests.py` assigns files before pytest or testmon can omit stable tests.
All jobs compute the same disjoint partition from the complete file list and committed
`.test_durations`. Files with recorded durations are balanced by their aggregate time;
new files use the suite's mean file time. A suite with no timing history uses file sizes.
`just test-durations-postgres` refreshes the existing unit timing file.

This ordering matters: selecting tests first and then splitting each job's different
collection can omit tests. The partition also keeps tests sharing a file together.

Current parallelism:

| Suite | Shards per runtime |
| --- | ---: |
| SQLite unit | 4 |
| SQLite integration | 2 |
| Postgres unit | 6 |
| Postgres integration | 4 |

Main records full dependency baselines. Branches select impacted tests, except changes
to test inputs, fixtures, dependencies, test infrastructure, or listed non-Python source
resources force full runs. Dependency tracking is an optimization, not a replacement
for the full main gate. Cache keys separate platform, Python version, suite, shard,
dependency/fixture versions, and partition implementation/timings.

`just test-cli-smoke` always runs the small CLI startup/exit suite, with testmon
disabled. CI runs it separately on every code change, including source-only PRs.
It covers help/version startup, initialization, schema validation, and reset exit
behavior. Other subprocess-only dependencies can still evade incremental selection;
the full main suite remains the broader check. This intentionally keeps the PR
smoke gate small rather than tracing every child process.

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
