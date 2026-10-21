# Large-project PostgreSQL FTS benchmark (#1791)

The multi-operand FTS path now collects per-document operand presence from GIN indexes.
It no longer re-reads each candidate's toasted chunks once per query operand for
filtering, again for synthetic ranking, and again for count. Parent and chunk matches
are grouped by `(project_id, id, type)`. The synthetic document keeps the original
operand order, so Boolean matching and rank calculations retain their semantics.
Pure/optional negation still includes scoped documents containing none of the operands.
Single-operand and mixed-script searches retain the existing query path.

## Reproduction

Use `scripts/benchmark_postgres_fts.py`; its module documentation includes the dedicated
Docker startup command. Do not use a development or production database. Seeding fails
if its fixture tables already exist.

```sh
uv run python scripts/benchmark_postgres_fts.py --seed
uv run python scripts/benchmark_postgres_fts.py \
  --restart-container bm-fts-bench --output /tmp/fts-before.json
# Switch to the changed compiler, keeping the same fixture.
uv run python scripts/benchmark_postgres_fts.py \
  --restart-container bm-fts-bench --output /tmp/fts-after.json
```

The fixture contains 32,000 documents across four projects and 320,000 chunks. Each
chunk includes 128 distinct hash lexemes to exercise TOAST instead of a tiny,
highly-compressible vector. Five percent of documents contain `meeting`, `budget`, and
`review`, distributed across different chunks. The statements use the production DDL,
query compiler, and text-query planner setting. They measure the SQL ranking and count
stages, not API hydration, embeddings, HNSW, or the relaxed retry's second statement.

Each statement runs twice. `--restart-container` resets PostgreSQL shared buffers
before the first run. It **does not clear the host/VM OS cache and does not reproduce
Neon's local file cache or pageserver latency**. Buffer counts are the primary evidence.
The JSON retains each full `EXPLAIN (ANALYZE, BUFFERS)` result. Root shared hits plus
shared reads count buffer accesses, not unique pages; do not sum inclusive child nodes.

## Measured results, 2026-10-20

Baseline: core `976a10c89`, including #1793, #1794, and #1795.
PostgreSQL 17 / pgvector container, one CPU, 2 GB memory, 128 MB shared buffers,
4 MB work memory, parallel query and JIT disabled. Same database for both runs.

Second-run buffer touches:

| Strict query / stage | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| `meeting budget review` page | 225,890 | 94,910 | 58% |
| `meeting budget review` exact count | 70,162 | 3,999 | 94% |
| `missingword budget` page | 114,076 | 2,081 | 98% |
| `missingword budget` exact count | 114,076 | 1,302 | 99% |
| `meeting` page | 146,967 | 146,967 | unchanged |
| `archive` page | 3,125,380 | 3,125,380 | unchanged |

Observed second-run latency for the three-word page went from 421 to 295 ms; its
count went from 68 to 20 ms. The missing-term page went from 293 to 15 ms. These are
individual local observations, not latency guarantees: host activity and OS-cache
state varied, so page-access reductions carry more weight than timings.

All result identities and scores matched the old compiler for six full-result queries
on the large fixture: `meeting budget review`, `budget review`, `meeting OR budget`,
`meeting NOT budget`, `meeting missingword`, and `meeting budget budget`.

The small Postgres regression suite exercises cross-chunk matching, negation including
all-negative queries, prefixes, repeated words, relaxed fallback, candidate keys, and
same-numbered rows in different projects/types. Its EXPLAIN regression fails with the
old compiler because operand chunk scans repeat per candidate; the new plan scans each
operand's chunk matches once through GIN.

## Remaining work

This is a partial improvement to #1791, not a claim that search is now uniformly fast.
Broad single-word ranking (`archive`) still touched 3.1 million buffers and took
30–40 seconds locally. Its remaining cost is chunk relevance ranking; this change does
not cap candidates or change ranking to conceal that cost. The exact-count API contract
is unchanged. Vector admission's cold-cache target and HNSW manifest heap reads also
remain separate work, as does production verification of the combined fixes.
