# Read-path load benchmark

This benchmark measures repeated, direct-permalink `read_note` calls through the real
`bm mcp` stdio server. It compares an authoritative warm baseline with the same workload after
the standalone Redis read cache is warmed.

## Workload

- deterministic 1, 16, and 64 KiB Markdown notes;
- 32 distinct notes per size;
- 128 measured reads at concurrency 1, 8, 32, and 64;
- corpus materialization, indexing, connection setup, and cache warmup outside measurement;
- isolated Basic Memory config, database, project, and home directories for every run;
- JSONL output with p50, p95, p99, throughput, response bandwidth, errors, and workload metadata;
- a `manifest.json` beside each result with benchmark and Basic Memory SHAs, provider versions,
  selected Redis and database server versions, target and harness Python versions, dirty-worktree
  state, synthetic-corpus checksum, and runtime configuration.

Every inherited `BASIC_MEMORY_*` setting is removed before the harness adds its explicit isolated
configuration. The harness explicitly disables Basic Memory auto-update so measurement cannot
start background package checks or upgrades. The cached run sets `BASIC_MEMORY_REDIS_URL` only in
the spawned MCP process and sizes `BASIC_MEMORY_REDIS_MAX_CONNECTIONS` to the largest requested
workload or seed concurrency. This prevents high-concurrency rows from exhausting the default pool
and silently measuring the cache's authoritative-read fallback. Output records whether Redis was
enabled and the non-secret pool size without recording the URL, because URLs may contain
credentials.

The Basic Memory SHA comes from the `basic_memory` module imported by the Python environment behind
`--bm-command`, not from the console script's parent directory. Publishable per-ref environments
must therefore use an editable install linked to the source checkout; the harness fails rather
than attributing a wheel or unrelated enclosing repository to the wrong SHA.

## Run a paired comparison

Use the same Redis server for the cached repetitions, but use a distinct scratch directory for
every run. The server must be ready before starting the benchmark; its startup time is not part
of the measurement.

```bash
just bench-read-cache redis://127.0.0.1:6379/0 run-01
```

The recipe writes each side's `results.jsonl` and `manifest.json` under
`.scratch/read-load-{authoritative,redis-warm}-run-01/`, then prints the Markdown comparison. Give
each repetition a distinct run ID so its corpus, results, and provenance remain available. Use
`just bench-read-load <label> [redis_url]` when running one side independently.
When invoking the script with a custom `--output` outside `--scratch`, `manifest.json` follows the
JSONL file into that output directory so retained results never lose their provenance.
The output path must be new unless `--truncate` is explicit; otherwise the harness fails before
starting runtime resources so one manifest cannot describe JSONL rows appended by multiple runs.
Each custom output directory is one run-artifact boundary: a second output filename is rejected
when that directory already contains `manifest.json`, preserving the first result's provenance.
Custom output and manifest paths must also stay outside the benchmark's runtime-owned `config`,
`project`, and `main-home` directories. Otherwise Basic Memory could index a result while the run
is active and invalidate the Redis entries being measured.

Use `just bench-read-smoke [redis_url]` for a tiny real-MCP run at concurrency 64 that verifies
Redis pool sizing plus result and manifest generation before committing to the full matrix.

Latency is evidence, not a CI threshold. Use at least six paired repetitions before making a
performance claim, alternate run order, and discard runs with material host contention. The
manifest must report clean benchmark and Basic Memory worktrees for a publishable comparison. The
real-Redis integration suite remains the correctness gate for cache identity and invalidation.
Run that gate with `just test-read-cache`.
