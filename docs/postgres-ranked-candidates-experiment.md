# Approximate single-word PostgreSQL ranking experiment

2026-10-21. Local research for issue #1791, separate from PR #1802. The user
approved approximate top results when that makes search substantially faster.
No application query, migration, or production database was changed here.

## Recommendation

Use RUM-ranked parent and chunk hits to select a bounded set of eligible source
hits, calculate the existing `ts_rank` on those hits, and aggregate the maximum
score per `(project_id, id, type)`. Start evaluation with 500 parent hits and
2,000 chunk hits for the application's 130-result retrieval window.

This was the best tested speed/quality tradeoff. It is still approximate: RUM's
ordering differs from `ts_rank`, especially for prefixes, and an omitted chunk
can change a note's score. Synthetic agreement is not a guarantee of real-world
relevance. Keep exhaustive retrieval available for unsupported query forms and
exact counts. Do not install this experimental compiler as a general replacement.

## Measurements

Dedicated PostgreSQL 17 container, 1 CPU, 2 GB memory, 128 MB shared buffers,
4 MB work_mem, parallel query and JIT disabled. Synthetic 32,000-note,
320,000-chunk fixture across four projects; large vectors deliberately exercise
TOAST. Runs were serial. Times below are SQL execution, excluding API/network,
result hydration and exact counts. There was no controlled OS-cache reset.

| Approach | Broad `archive` | Finding |
| --- | ---: | --- |
| Existing exhaustive ranking | 36.84 s | Reads/ranks all matching chunks |
| Batched ranking, original join plan | 102.02 s | Bad nested loop amplifies work |
| Batched ranking, hash join experiment | 34.44 s | Still fetches/ranks all vectors |
| Inline vector copy | 16.14 s | Adds about 2.5 GB of heap |
| Parent-ranked bounded rerank | 0.93 s | Misses important late/prefix matches |
| RUM source hits, budget 500 | 1.164 / 0.665 / 0.642 s | First / second / third run |

RUM `meeting`: 0.830 / 0.070 / 0.027 s. All final runs returned 130 notes.
The final `archive` plan touched 21,881 inclusive shared buffers, versus about
3,125,380 for exhaustive ranking. These are buffer accesses, not unique pages.
Historical alternatives were measured in separate runs; this is not a randomized
production benchmark. The large fixture has homogeneous relevance and mainly
measures I/O behavior.

## Quality and filters

A separate fixture contains 2,000 notes and 8,000 chunks, varying term frequencies,
late matches beyond the 6,000-character parent projection, title matches, and
multiple prefix-matching lexemes. Nine combinations cover `archive`, `arch*`, and
`meeting`, across all four projects and two individual projects.

For top 20, budgets of 250 parent / 1,000 chunk hits matched all nine exhaustive
results. For top 130, that budget was insufficient (worst score-based NDCG 0.844).
At 500 parent / 2,000 chunk hits, all nine cases matched the exhaustive top 130
identities and scores. This NDCG uses existing `ts_rank` scores as gains; it is
not a human relevance evaluation. Some scoped queries have only 100 matches.

Eighteen additional checks covered project scope, a late-body permalink match,
title, metadata, path, updated date, and item type. Restrictive result sets that
fit the budget matched exhaustive results; broad filtered searches returned the
requested count and stayed within scope. Category and valid-time filters,
large duplicate-heavy documents, deep pagination, mixed scripts, concurrency,
and a representative real corpus remain unvalidated.

## Why the query shape matters

The prototype applies structured filters before each source budget. Chunk hits
use a correlated parent eligibility `EXISTS` with `OFFSET 0`; without the fence,
the planner reversed the join and fetched every parent's chunks. Filtering only
after the budget can wrongly underfill restrictive searches.

Multi-project `IN` as a RUM index condition lost ordered scans in these plans.
The experiment keeps that predicate as a filter with `(project_id + 0) IN (...)`.
Single-project searches retain ordinary indexed equality. Scope is enforced in
both cases, but globally scanning ranked hits may be expensive for a small scope
inside a much larger database. This needs a skewed-project benchmark.

Transaction-local `enable_seqscan=off` and `enable_bitmapscan=off` preserve ordered
RUM index scans. Final plans read at most 500 parent and 2,000 eligible chunk hits
before aggregation. Eligibility probes may inspect more rejected hits. These
planner settings must remain local to the experimental strategy.

## Reproduce

Seed the dedicated local database using `scripts/benchmark_postgres_fts.py` and
its companion `docs/postgres-fts-large-project-benchmark.md`. Install the
`postgresql-17-rum` package in that disposable PostgreSQL image (tested 1.3.15),
then run:

```sh
uv run python scripts/benchmark_postgres_ranked_candidates.py --create-indexes
uv run python scripts/benchmark_postgres_ranked_candidates.py \
  --budget 500 --limit 130 --output /tmp/rum-final.json
```

The script rejects remote hosts and non-single-operand queries. It creates indexes
only with the explicit flag and writes full EXPLAIN ANALYZE BUFFERS JSON plans.
It is a SQL benchmark, not a production adapter. The quality setup, comparative
runners, and raw plans from this investigation are preserved in the machine-local
`/Users/phernandez/dev/basicmachines/search-1791-experiments` directory. They are
research artifacts, not a checked-in regression suite.

## Integration cost and remaining work

The chunk RUM index was approximately 330 MB versus 181 MB for GIN, and took
about six minutes to build on this constrained fixture. RUM also has higher write
cost; measure ingestion and index maintenance before enabling it. Neon documents
RUM for PostgreSQL 14–17, but not 18; verify the actual hosted engine and extension
version before designing a rollout. No live cloud version was inspected.

Next implement an explicit, optional ranked retrieval strategy with schema/index
provisioning, query eligibility, bounded result hydration and pagination behavior.
Preserve existing exact counts and unsupported-query behavior. First add skewed
project and duplicate-heavy document fixtures, then measure full request latency
and relevance on representative data. Keep this separate from the exact multiword
GIN optimization already in PR #1802.

## Sources

- [PostgreSQL ranking](https://www.postgresql.org/docs/17/textsearch-controls.html#TEXTSEARCH-RANKING): ranking consults matching vectors and can be I/O bound.
- [RUM](https://github.com/postgrespro/rum): positional index data and ranked scans; higher build/write costs. Use `rum_tsvector_ops` for prefix support.
- [Neon extensions](https://neon.com/docs/extensions/pg-extensions): hosted extension/version availability.
- [PostgreSQL ts_rank source](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/backend/utils/adt/tsrank.c): prefix matches can sum multiple lexeme contributions; indexing only repeated lexemes would not preserve current prefix ranking.
- [Neon lakebase_text](https://neon.com/docs/extensions/lakebase-text): BM25 with Block-Max WAND is another possible direction, but changes ranking and was not benchmarked here.
