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
- JSONL output with p50, p95, p99, throughput, response bandwidth, errors, and workload metadata.

The uncached run removes any inherited `BASIC_MEMORY_REDIS_URL`. The cached run sets it only in
the spawned MCP process. Output records whether Redis was enabled without recording the URL,
because URLs may contain credentials.

## Run a paired comparison

Use the same Redis server for the cached repetitions, but use a distinct scratch directory for
every run. The server must be ready before starting the benchmark; its startup time is not part
of the measurement.

```bash
just bench-read-cache redis://127.0.0.1:6379/0 run-01
```

The recipe writes `.scratch/read-load-authoritative-run-01.jsonl` and
`.scratch/read-load-redis-warm-run-01.jsonl`, then prints the Markdown comparison. Give each
repetition a distinct run ID so its workload and JSONL artifacts remain available. Use
`just bench-read-load <label> [redis_url]` when running one side independently.

Latency is evidence, not a CI threshold. Use at least six paired repetitions before making a
performance claim, alternate run order, and discard runs with material host contention. The
real-Redis integration suite remains the correctness gate for cache identity and invalidation.
Run that gate with `just test-read-cache`.
