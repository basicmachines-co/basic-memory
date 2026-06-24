# Write-path load benchmark (branch/SHA comparison)

Living design + decisions + run log for the write-path/load benchmark used to
compare Basic Memory performance across git refs (e.g. `main` vs the
accepted-note refactor branch).

## Goal

Measure how the local write path behaves **under concurrency/load**, and
compare two installed versions of `basic-memory`. The hypothesis we want to
prove or disprove:

> The DB-first **accept** holds the write lock briefly and pushes
> materialize / index / embed onto background work, so the system should
> **sustain more concurrent writers before accept-latency degrades or writes
> start blocking** — whereas the synchronous (`main`) write does parse → write
> file → index → embed inline before returning, so under load requests pile up
> (and on SQLite the single-writer lock makes that stark).

## Key decisions

1. **Drive over MCP stdio (`bm mcp`).** Spawning `bm mcp` brings up the *full*
   local runtime — the file watcher **and** the background schedulers
   (project-index, vector-sync, relation-resolution). That's exactly the async
   machinery the refactor introduced, so `write_note` over MCP exercises the
   real accept → materialize → background-followup path. CLI / in-process ASGI
   would each need us to hand-start the watcher and flip `env` out of test mode.

2. **Branch comparison via per-ref venvs (not worktrees).** Each ref gets its
   own venv with `basic-memory@<sha>` installed from GitHub
   (`uv pip install 'basic-memory @ git+https://github.com/basicmachines-co/basic-memory@<ref>'`).
   The driver points the spawned `bm mcp` at that venv's `basic-memory`
   executable (`--bm-command <venv>/bin/basic-memory`). The harness (this
   driver) stays fixed; only the spawned `bm` varies → any two refs, repeatable.

3. **Isolated runtime per run.** `BASIC_MEMORY_CONFIG_DIR` scopes config / DB /
   project registry to a fresh scratch dir, and `BASIC_MEMORY_ENV` is **unset**
   (so it is not `test` — test mode disables the watcher + schedulers, which
   would defeat the whole measurement). A fresh home also avoids alembic
   migration rot between versions.

4. **Synthetic test driver for now.** We generate deterministic markdown notes
   (frontmatter + observations + typed relations) on the fly. This keeps the
   workload controllable (size, concurrency) and reproducible. We can later point
   the same driver at a real corpus (an existing BM project, or the vendored
   LoCoMo/synthetic corpora) for realism.

5. **Standard JSONL output.** Each scenario emits one
   `{"benchmark": "...", "metrics": {...}, "timestamp_utc": "..."}` line, using
   the metric-name conventions the generic compare tool understands
   (`*_ms` → lower better, `*_per_sec` → higher better). Compare two refs with
   `basic-memory/test-int/compare_search_benchmarks.py` (or the package's
   `bm-bench compare`).

## Metrics (per concurrency level C)

| metric | meaning | better |
| --- | --- | --- |
| `accept_latency_p50/p95/p99_ms` | per `write_note` call (the caller-perceived accept) | lower |
| `accept_throughput_per_sec` | notes accepted / wall-clock at concurrency C | higher |
| `accept_error_rate` | failed/timed-out writes / total | lower |
| `time_to_materialized_ms` | after the burst, until all N files exist on disk | lower |
| `time_to_searchable_ms` | after the burst, until all N notes are FTS-searchable | lower |

`time_to_embedded_ms` (semantic) is a planned follow-up (requires fastembed +
sqlite-vec in the venv and a DB/vector peek).

## Concurrency sweep

`C ∈ {1, 4, 8, 16, 32}`; fixed burst of N writes per level (unique titles).
The "knee" — the C where throughput plateaus or p95 crosses a budget — is the
"before falling over" point per ref.

## How to run

```bash
cd benchmarks

# 1) Build a per-ref venv (installs basic-memory@<ref> from GitHub)
just bench-venv main
just bench-venv codex/repository-explicit-sessions

# 2) Run the write-load sweep against a ref's venv
just bench-write-load main
just bench-write-load codex/repository-explicit-sessions

# 3) Compare the two JSONL outputs
uv run python ../test-int/compare_search_benchmarks.py \
  .scratch/write-load-main.jsonl \
  .scratch/write-load-branch.jsonl --format markdown
```

## Run log

### 2026-06-24 — main baseline (SQLite, semantic on)

Ref: `main` (basic-memory 0.22.1, fastembed bge-small-en is a transitive dep so
embedding is part of the measured path). 60 writes/level, warmup 8 (model
pre-loaded + drained before measuring).

| C | accept p50 (ms) | accept p95 (ms) | throughput (/s) | errors | time→searchable (ms) |
| --- | --- | --- | --- | --- | --- |
| 1 | 91 | 416 | 4.8 | 0 | 63 |
| 4 | 465 | 1839 | 5.5 | 0 | 36 |
| 8 | 876 | 2762 | 6.8 | 0 | 125 |
| 16 | 1966 | 3928 | 6.6 | 0 | 67 |
| 32 | 2530 | 5793 | 6.8 | 0 | 129 |

**Interpretation (main = synchronous write path):**
- Throughput saturates by C≈8 (~6.7/s); extra concurrency does **not** lift it.
- Accept latency grows ~28× from C=1→C=32 (p50 91ms → 2.5s; p95 416ms → 5.8s):
  each `write_note` does parse → write file → index → embed inline, so under
  load writes queue behind the SQLite single-writer lock + embedding.
- `time_to_searchable` is small/flat (synchronous: the note is basically
  indexed by the time `write_note` returns).
- No errors at these levels — it degrades by latency, not by failing (yet).

This is the baseline the async accepted-note path should beat: flat/low accept
latency as C rises, higher sustained throughput, with the heavy work deferred
to background follow-ups (so `time_to_searchable`/`time_to_embedded` become the
measurable lag instead of accept latency).

**Measurement notes / gotchas found:**
- The fastembed model download must be fully outside measurement; the warmup now
  writes + waits-for-searchable so the model is loaded before level 1.
- A cosmetic "Process group termination failed … Operation not permitted" line
  prints on teardown (macOS sandbox); the run still completes.

## Open questions / next steps

- Add `time_to_embedded_ms` (install fastembed + sqlite-vec; peek `search_vector_chunks`).
- Add a "writes-during-drain" probe (fire writes while the backlog drains; do they stay fast?).
- Add Postgres backend (cloud-relevant concurrency headroom).
- Decide whether to fold the driver into `bm-bench run write-load` (CLI subcommand) vs keep standalone.
