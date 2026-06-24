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

### 2026-06-24 — main vs branch (`codex/repository-explicit-sessions`, PR #1002)

Same harness, same params (60 writes/level, C∈{1,4,8,16,32}, SQLite, semantic
on). Branch venv installed from GitHub. **Single run each — directional, not
statistically settled.**

| C | accept p50 main→branch | throughput main→branch | verdict |
| --- | --- | --- | --- |
| 1 | 92 → 322 ms (+252%) | 4.8 → 2.2 /s (-54%) | branch slower |
| 4 | 465 → 801 ms (+72%) | 5.5 → 3.9 /s (-29%) | branch slower |
| 8 | 876 → 1488 ms (+70%) | 6.8 → 4.4 /s (-35%) | branch slower |
| 16 | 1966 → 2781 ms (+41%) | 6.6 → 4.7 /s (-29%) | branch slower |
| 32 | 2530 → 4135 ms (+63%) | 6.8 → 5.3 /s (-21%) | branch slower |

**The async-accept hypothesis did NOT hold on local SQLite.** The accepted-note
branch is consistently slower per accept and lower throughput at every level
(0 errors on both). `time_to_searchable` stays small on both, so the deferred
work is not what's showing up — the **accept path itself got heavier**.

Leading hypotheses (to isolate next):
1. **Per-write full-project relation resolution (#1015).** The branch schedules
   `resolve_project_relations` (a *whole-project* unresolved-relation scan) as a
   background task on **every** write. Under a 60-write burst that's 60 scans
   over a growing relation set, all contending on the SQLite writer — likely the
   dominant cost. The fix's own issue notes the resolver supports coalescing; we
   scheduled it per-write **without debounce**. Strong suspect.
2. **Accepted-note DB overhead.** DB-first accept persists an extra `note_content`
   row (plus entity + search) per write — more writes per accept = longer SQLite
   write-lock hold = more contention under concurrency.
3. **The "offline queue" win is cloud-specific (PGQ).** Locally there is no
   durable queue; materialization runs inline and the schedulers are in-process
   asyncio tasks. So the throughput benefit the hypothesis assumed may simply not
   exist on the local SQLite runtime — it would show on Postgres + PGQ.

This is a useful result: the benchmark immediately surfaced a probable
write-load regression in the new path (and specifically in the per-write
relation-resolution scheduling added for #1015).

### 2026-06-24 — coalescing fix did NOT close the gap (hypothesis falsified)

Pushed `perf(core): coalesce per-write relation resolution` (7ecb672c) so a
burst collapses to one debounced offline pass, reinstalled the branch venv, and
re-ran. The gap is essentially unchanged (within run noise):

| C | branch p50 before → after fix | main p50 | still worse |
| --- | --- | --- | --- |
| 1 | 322 → 294 ms | 92 | +220% |
| 8 | 1488 → 1563 ms | 876 | +78% |
| 32 | 4135 → 4663 ms | 2530 | +84% |

**Why it didn't help:** the relation passes were always **background** (async
tasks) — they add system/DB load but do not sit on the synchronous accept
latency we measure. The tell is **C=1**: with zero concurrency/contention the
branch is still ~3× slower per write (92 → 294 ms). That is the **synchronous
accept path itself** being heavier, not the follow-ups.

**Revised conclusion:** the local write regression is in the accepted-note
accept path, not the schedulers. Locally the path pays for **both** the DB-first
accept (extra `note_content` row + mutation-runner machinery) **and** inline
materialization — whereas the cloud design's win is to *skip* inline
materialization and defer it to a PGQ queue. That queue benefit does not exist on
the local SQLite runtime, so the refactor is net heavier for local writes.

The coalescing change is still worth keeping (it removes genuinely redundant
whole-project scans and lowers background DB contention) but it is not the fix
for accept latency. This is the benchmark working as intended — it tested a fix
and showed it did not address the measured cost.

### 2026-06-24 — deferring materialization recovered (and flipped) the result

Pushed `perf(core): defer local note materialization off the accept path`
(696d71b1): local `materialize_write_change` now schedules the file write +
index as a background task (parity with cloud's PGQ enqueue) and returns the
accepted note_content state at once. Reinstalled the branch venv, re-ran.

| C | branch p50 before→after defer | main p50 | branch vs main | throughput (branch vs main) |
| --- | --- | --- | --- | --- |
| 1 | 322 → 113 ms | 92 | +23% slower | 3.5 vs 4.8 |
| 4 | 801 → 647 ms | 465 | +39% slower | 5.3 vs 5.5 |
| 8 | 1488 → 903 ms | 876 | ~parity | 6.7 vs 6.8 |
| 16 | 2781 → 1072 ms | 1966 | **45% faster** | **7.2 vs 6.6** |
| 32 | 4135 → 1938 ms | 2530 | **23% faster** | **7.6 vs 6.8** |

**The hypothesis now holds.** Deferring the file write + index off the accept
path cut branch accept p50 by ~50-60% at C≥16 and made throughput **scale with
concurrency** (3.5 → 7.6/s) instead of plateauing like main's synchronous path
(~6.7/s). Crossover ~C=8 — below it main's lighter per-write path is marginally
faster, above it the deferred accept wins on both latency and throughput.

Residual: at C=1 the branch is still ~+21ms (113 vs 92) — the intrinsic DB-first
accept overhead (extra `note_content` row + mutation-runner machinery), the
cloud-architecture tax, small in absolute terms and the price of one read model
+ parity. `time_to_searchable` rises under load (index is now async: ~0.5s at
C=32), the expected "DB is the cache, file+index catch up" tradeoff.

Conclusion: the accepted-note refactor is a **net win for concurrent local
writes** once materialization is deferred, and a small per-write cost at very low
concurrency. Methodology + harness proven end to end.

## Open questions / next steps

- **Profile the synchronous accept path** (C=1, single write): where do the
  ~200ms over main go? Candidates: the extra `note_content` insert, the
  mutation-runner/preparer layers, extra DB round-trips, checksum/permalink work.
- Product call: is a slower *local* write an acceptable tradeoff for the cloud
  architecture, or does the local accept path need a lighter route (e.g. skip the
  note_content round-trip when materializing inline anyway)?
- Repeat runs (3x) for variance bounds.

- Add `time_to_embedded_ms` (install fastembed + sqlite-vec; peek `search_vector_chunks`).
- Add a "writes-during-drain" probe (fire writes while the backlog drains; do they stay fast?).
- Add Postgres backend (cloud-relevant concurrency headroom).
- Decide whether to fold the driver into `bm-bench run write-load` (CLI subcommand) vs keep standalone.
