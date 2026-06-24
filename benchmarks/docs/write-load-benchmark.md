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

Conclusion (moderate concurrency): the accepted-note refactor is a **net win for
concurrent local writes** once materialization is deferred, and a small per-write
cost at very low concurrency. **But see the high-concurrency caveat below** — the
deferral has no backpressure, so the picture changes under heavy sustained load.

### 2026-06-24 — high-concurrency scaling: the async deferral hits an unbounded-queue wall

Pushed concurrency to 128 (notes=200) to find where SQLite's single writer
chokes. The deferral helps the **median** at every level, but the **tail and
throughput regress under heavy sustained load** because the fire-and-forget
background materializations form an *unbounded* queue with no backpressure.

| C | branch p50 (ms) | branch p99 (ms) | throughput /s | err rate |
| --- | --- | --- | --- | --- |
| 1 | 113 | 1249 | 5.5 | 0.000 |
| 16 | 2718 | 7991 | 5.0 | 0.000 |
| 32 | 4803 | 16362 | 5.1 | 0.000 |
| 64 | 5201 | **121771** | **1.5** | **0.020** |
| 128 | 5737 | **94954** | **2.0** | **0.015** |

vs main at C=64: p50 9616 / p99 19130 / 5.5 thru / 0 err. The branch's **median
is far better** (5.2s vs 9.6s) but its **p99 is 6× worse** (122s vs 19s),
throughput collapses (1.5 vs 5.5/s), and it starts failing writes (SQLite
"database is locked"). Main's synchronous accept is self-throttling, so its tail
stays bounded.

**Mechanism:** every accept schedules `asyncio.create_task(materialize)`. The
accept returns fast, but the background tasks pile up unbounded and contend with
incoming accepts for the **single SQLite writer** and the **single event loop**.
Past ~C=64 the backlog starves the request handler → tail explosion + lock
errors. The deferral *reorders* work; on one SQLite it does **not** add capacity.

The collapse is load-/depth-dependent: a lighter run (notes=100) reached C=64
with no errors and rising throughput, while notes=200 collapsed at C=64 — bigger
sustained bursts → deeper backlog → worse tail.

**Implication:** the real scaling fix is **bounded** background materialization —
a worker pool / semaphore that caps in-flight materializations and applies
backpressure (the accept slows instead of the backlog growing unbounded). That
bounds the tail and keeps throughput from collapsing. Next experiment.

### 2026-06-24 — SQLite vs Postgres (async writes, local): Postgres collapses on NullPool

Ran the branch's async write path on SQLite vs a local Postgres (pgvector
testcontainer), notes=100. Two local-Postgres bugs had to be cleared first (see
below); after that:

| C | p50 ms (sql / pg) | p99 ms (sql / pg) | thru/s (sql / pg) | err (sql / pg) |
| --- | --- | --- | --- | --- |
| 1 | 121 / 548 | 1686 / 1361 | 4.1 / 1.7 | 0 / 0 |
| 8 | 1063 / 3336 | 3900 / 5002 | 5.9 / 2.3 | 0 / 0 |
| 32 | 2037 / **9045** | 10335 / **478612** | 6.6 / **0.2** | 0 / **0.21** |
| 64 | 1846 / **93920** | 7570 / 101470 | 10.6 / **0.9** | 0 / 0.04 |

Result is the **opposite of "on par"**: local Postgres is ~4× slower at C=1
(round-trips vs in-process SQLite) and **collapses** at C≥32 — p99 of 478s,
0.2/s throughput, 21% write failures, and the materialization backlog never
drains within 300s.

**Cause:** `db._create_postgres_engine` uses `poolclass=NullPool` — a fresh
connection per request (fine for cloud's per-tenant, low-local-concurrency
model). Under concurrent local writes — plus each background materialization
opening its own connection — that's a connection storm against the default
`max_connections=100`: 25 writes failed in the Postgres phase. MVCC's
concurrent-writer advantage is completely swamped by connection churn.

**Implication (mirrors the SQLite finding):** the write path needs **resource
bounds** on both axes — bounded background materialization concurrency *and*
connection pooling for Postgres. A pooled, co-located cloud Postgres would look
very different; local NullPool Postgres is not a viable high-concurrency backend
as configured.

**Local-Postgres bugs found via this benchmark:**
1. *Startup migration crash under uvloop* — `alembic/env.py` applied
   `nest_asyncio` under the uvloop policy; the resulting "this event loop is
   already running" string evaded the thread-based fallback, crashing `bm mcp`
   against Postgres on startup. **Fixed** in `fix(core): run local Postgres
   startup migrations under uvloop` (`basic_memory.migration_loop`, 6 unit tests).
2. *No default project for a fresh local Postgres* — `create_memory_project`'s
   default lookup raises because the Postgres path uses a cloud-style config
   manager that seeds no default and nothing seeds a DB default locally. Worked
   around in the harness with `BASIC_MEMORY_DEFAULT_PROJECT`; the product gap
   (local Postgres project bootstrap) is left as a finding.

### 2026-06-24 — bounded materialization worker pool FIXES the wall

Replaced fire-and-forget `asyncio.create_task` with a config-sized worker pool
(`materialization_workers`, default 4; `_MaterializationWorkerPool`). Re-ran the
C=128 sweep (notes=200):

| C | no-pool p99 → pool p99 | no-pool thru → pool thru | errors |
| --- | --- | --- | --- |
| 64 | 121771 → **9870** ms (12×) | 1.5 → **11.9**/s (8×) | 2% → **0** |
| 128 | 94954 → **11852** ms (8×) | 2.0 → **10.9**/s (5×) | 1.5% → **0** |

Throughput now **scales and plateaus** at ~11/s (C=8 10.3 → C=128 10.9) instead
of collapsing; p99 grows gracefully (2.6 → 12s); **zero errors** at every level;
backlog drains fast (`time_to_searchable` 35-98ms). The pooled branch also
**beats main** at high concurrency (main C=128: p50 16.2s / thru 4.6 / vs pool
p50 4.5s / thru 10.9). Bounding the background work is what makes the async write
path actually scale.

**Worker-count tuning (notes=200, C=64/128):**

| workers | thru/s (C=64 / C=128) | p99 ms (C=64 / C=128) |
| --- | --- | --- |
| 2 | **15.6 / 16.0** | **7263 / 10517** |
| 4 | 11.9 / 10.9 | 9870 / 11852 |
| 8 | 11.0 / 11.0 | 12307 / 13855 |

**2 workers is the SQLite sweet spot** — the single writer is the bottleneck, so
fewer workers thrash it less. More workers just contend. Postgres (concurrent
writers, once pooled) should want a higher count — which is exactly why
`materialization_workers` is configurable. Default left at 4 (resilient middle);
tune per backend.

### 2026-06-24 — Postgres connection pool FIXES the collapse (+ default-project seed)

Wired `_create_postgres_engine` to the existing `db_pool_size`/`db_pool_overflow`/
`db_pool_recycle` config (`AsyncAdaptedQueuePool` instead of `NullPool`), and
gated the config's default-project seeding on `skip_initialization_sync` instead
of the Postgres backend, so a fresh local Postgres seeds `main` and no longer
needs the `BASIC_MEMORY_DEFAULT_PROJECT` shim. Re-ran sqlite vs postgres with the
worker pool (4) on both:

| C | p99: NullPool → pooled (pg) | thru: NullPool → pooled (pg) | err (pg) |
| --- | --- | --- | --- |
| 32 | 478612 → **2021** ms (237×) | 0.2 → **21.9**/s (100×) | 21% → **0** |
| 64 | (p50 93920 → 2043) | 0.9 → **21.5**/s | 4% → **0** |

Postgres no longer collapses: it scales cleanly to ~21/s with **zero errors** and
without the shim. Full pooled run:

| C | p50 sql/pg | p99 sql/pg | thru sql/pg (per s) |
| --- | --- | --- | --- |
| 1 | 470 / 133 | 3597 / 597 | 1.4 / 6.1 |
| 8 | 2305 / 468 | 7211 / 1586 | 3.2 / 14.5 |
| 32 | 1204 / 1001 | 4010 / 2021 | 12.6 / 21.9 |
| 64 | 1642 / 2043 | 7346 / 2978 | 11.8 / 21.5 |

Caveat: this single run's **SQLite was anomalously slow** (C=1 p50 470ms vs
~100ms elsewhere) — machine load from back-to-back container runs. The 3× repeat
below confirms it was noise.

### 2026-06-24 — 3× repeat (variance bounds): on par at C=1, Postgres scales better

Ran sqlite vs postgres 3× each, fresh DB per run, notes=100. mean [min-max]:

| C | SQLite p99 ms | Postgres p99 ms | SQLite thru/s | Postgres thru/s |
| --- | --- | --- | --- | --- |
| 1 | 1310 [1063-1535] | 911 [653-1254] | 5.2 [4.5-6.4] | 6.1 [5.6-6.5] |
| 8 | 3100 [2718-3642] | 1385 [908-2275] | 9.0 [8.3-9.8] | 14.2 [10.0-17.7] |
| 32 | 5226 [4456-6077] | 2463 [2139-2818] | 12.8 [11.3-13.6] | 18.3 [14.9-21.7] |
| 64 | 7487 [5483-10541] | 3488 [2952-4240] | 12.2 [8.6-14.2] | 20.5 [17.7-22.6] |

(p50 — SQLite: 95/608/1497/2254 ms; Postgres: 117/492/1184/2300 ms at C=1/8/32/64.)

Findings, now with bounds:
- **Zero failed writes across all 24 cells.** Both backends are stable under load
  with the worker pool + (for Postgres) connection pool. No collapse anywhere.
- **The earlier SQLite "470ms" was noise** — clean SQLite C=1 p50 is ~95ms.
- **On par at C=1** (sqlite 95 vs pg 117 ms p50; throughput ranges overlap).
- **Postgres scales better at concurrency, and the ranges separate** — at C=64
  Postgres throughput is ~20.5 vs SQLite ~12.2/s and p99 ~3.5s vs ~7.5s, with no
  overlap at C=32/64. The MVCC + connection-pool advantage over SQLite's single
  writer is real and reproducible (matches the hypothesis: roughly on par locally,
  better headroom on Postgres) — once NullPool is replaced with a real pool.
- SQLite C=64 is the noisiest cell (p99 5.5-10.5s) — single-writer contention is
  the most load-sensitive, as expected.

### 2026-06-24 — controlled 2×2: main (direct writes) vs branch (async writes)

The three enabling/pool fixes were cherry-picked onto `main` (PR #1018) so `main`
can run a pooled local Postgres, giving a true apples-to-apples comparison.
`main` here = main's **direct/inline** write path (pre-#1002) + the 3 fixes;
`branch` = the #1002 **async** path (deferred materialization + worker pool +
connection pool). All four cells run back-to-back in one session, interleaved per
backend, notes=100. Zero write failures in all 16 cells.

Each metric below has its own `main` and `branch` row, plus the branch's
improvement (latency rows = main÷branch faster; throughput row = branch÷main
higher). Concurrency runs across the columns.

**SQLite — main (direct writes) vs branch (async writes)**

| metric | C=1 | C=8 | C=32 | C=64 |
| --- | --- | --- | --- | --- |
| p50 main (ms) | 224 | 1210 | 4944 | 7515 |
| p50 branch (ms) | 119 | 645 | 1436 | 1885 |
| **p50: branch faster** | **1.9×** | **1.9×** | **3.4×** | **4.0×** |
| p99 main (ms) | 1754 | 4934 | 12807 | 13742 |
| p99 branch (ms) | 1430 | 2980 | 4602 | 6656 |
| **p99: branch faster** | **1.2×** | **1.7×** | **2.8×** | **2.1×** |
| throughput main (/s) | 3.4 | 4.9 | 4.8 | 5.6 |
| throughput branch (/s) | 4.8 | 9.3 | 12.5 | 10.3 |
| **throughput: branch higher** | **1.4×** | **1.9×** | **2.6×** | **1.8×** |

**Postgres — main (direct writes) vs branch (async writes)**

| metric | C=1 | C=8 | C=32 | C=64 |
| --- | --- | --- | --- | --- |
| p50 main (ms) | 227 | 971 | 2491 | 7002 |
| p50 branch (ms) | 133 | 482 | 1323 | 2287 |
| **p50: branch faster** | **1.7×** | **2.0×** | **1.9×** | **3.1×** |
| p99 main (ms) | 1299 | 1782 | 3367 | 11292 |
| p99 branch (ms) | 743 | 1150 | 2682 | 3399 |
| **p99: branch faster** | **1.7×** | **1.5×** | **1.3×** | **3.3×** |
| throughput main (/s) | 3.5 | 7.6 | 10.8 | 7.1 |
| throughput branch (/s) | 5.7 | 14.2 | 18.2 | 18.5 |
| **throughput: branch higher** | **1.6×** | **1.9×** | **1.7×** | **2.6×** |

Conclusions:
- **Branch (async) beats main (direct) on both backends, at every level** — ~3-4×
  lower p50, ~2-3× lower p99, ~2× throughput at C=64.
- **main plateaus; branch scales.** main throughput tops out (~5/s SQLite, ~10/s
  Postgres) and on Postgres *drops* at C=64 (10.8→7.1); branch climbs and holds
  (10-18/s).
- **Postgres > SQLite for both, gap wider for the branch** (branch C=64: 18.5 vs
  10.3 /s) — the async pool exploits concurrent writers SQLite's single writer
  can't offer.
- **Supersedes the earlier "+21ms at C=1" note** — that was a cross-run artifact;
  in a controlled interleaved run the branch is faster at C=1 too (it defers the
  file write off the accept).

## Open questions / next steps

**Done:** worker pool, Postgres connection pool, local-Postgres default-project
seed, the migration-under-uvloop fix (`basic_memory.migration_loop`), and the
controlled main-vs-branch 2×2 (above). The 3 enabling fixes are PR #1018 to main.

**Remaining harness/measurement work:**
- Add `time_to_embedded_ms` (fastembed + sqlite-vec; peek `search_vector_chunks`).
- Decide whether to fold the driver into `bm-bench run write-load` (CLI subcommand).
