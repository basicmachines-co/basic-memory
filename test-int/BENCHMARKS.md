# Performance Benchmarks

This directory contains performance benchmark tests for Basic Memory search indexing and retrieval.

## Purpose

These benchmarks measure baseline performance to track improvements from optimizations. They are particularly important for:
- Local semantic search throughput and query latency
- Large repositories (100s to 1000s of files)
- Validating optimization efforts before and after ranking/indexing changes

## Running Benchmarks

### Run all benchmarks (excluding slow ones)
```bash
pytest test-int/test_search_performance_benchmark.py -v -m "benchmark and not slow"
```

### Run specific benchmark
```bash
# Cold indexing throughput (300 notes)
pytest test-int/test_search_performance_benchmark.py::test_benchmark_search_index_cold_start_300_notes -v

# Query latency for fts/vector/hybrid
pytest test-int/test_search_performance_benchmark.py::test_benchmark_search_query_latency_by_mode -v

# Retrieval quality (hit@1, recall@5, mrr@10) for lexical/paraphrase suites
pytest test-int/test_search_performance_benchmark.py::test_benchmark_search_quality_recall_by_mode -v

# Incremental re-index (80 changed notes out of 800)
pytest test-int/test_search_performance_benchmark.py::test_benchmark_search_incremental_reindex_80_of_800_notes -v -m slow
```

### Run all benchmarks including slow ones
```bash
pytest test-int/test_search_performance_benchmark.py -v -m benchmark
```

### Write JSON benchmark artifacts
```bash
BASIC_MEMORY_BENCHMARK_OUTPUT=.benchmarks/search-benchmarks.jsonl \
pytest test-int/test_search_performance_benchmark.py -v -m benchmark
```

### Compare two benchmark runs
```bash
uv run python test-int/compare_search_benchmarks.py \
  .benchmarks/search-baseline.jsonl \
  .benchmarks/search-candidate.jsonl \
  --show-missing

# via just
just benchmark-compare .benchmarks/search-baseline.jsonl .benchmarks/search-candidate.jsonl table --show-missing
```

Optional filters:
```bash
uv run python test-int/compare_search_benchmarks.py \
  .benchmarks/search-baseline.jsonl \
  .benchmarks/search-candidate.jsonl \
  --benchmarks "cold index (300 notes),query latency (hybrid)"
```

Markdown output for PR comments:
```bash
uv run python test-int/compare_search_benchmarks.py \
  .benchmarks/search-baseline.jsonl \
  .benchmarks/search-candidate.jsonl \
  --format markdown
```

### Skip benchmarks in regular test runs
```bash
pytest -m "not benchmark"
```

### Optional guardrails (recommended for nightly runs only)
```bash
BASIC_MEMORY_BENCH_MIN_COLD_NOTES_PER_SEC=80 \
BASIC_MEMORY_BENCH_MIN_INCREMENTAL_NOTES_PER_SEC=60 \
BASIC_MEMORY_BENCH_MAX_FTS_P95_MS=30 \
BASIC_MEMORY_BENCH_MAX_VECTOR_P95_MS=45 \
BASIC_MEMORY_BENCH_MAX_HYBRID_P95_MS=60 \
pytest test-int/test_search_performance_benchmark.py -v -m benchmark
```

Guardrails are opt-in. When threshold environment variables are not set, tests only report metrics.

## Benchmark Output

Each benchmark provides detailed metrics including:

- **Performance Metrics**:
  - Total indexing/re-index time
  - Notes processed per second
  - Query latency percentiles (p50/p95/p99)
  - Retrieval quality metrics (hit@1, recall@5, mrr@10)

- **Database Metrics**:
  - Final SQLite database size for the benchmark run

- **Operation Counts**:
  - Notes indexed
  - Notes re-indexed
  - Queries executed per retrieval mode

- **Optional JSON Artifacts**:
  - One JSON object per benchmark test run when `BASIC_MEMORY_BENCHMARK_OUTPUT` is set
  - Includes benchmark name, UTC timestamp, and metric values

## Example Output

```
BENCHMARK: cold index (300 notes)
notes indexed: 300
elapsed (s): 11.4820
notes/sec: 26.13
sqlite size (MB): 4.83

BENCHMARK: query latency (hybrid)
queries executed: 32
avg latency (ms): 3.40
p50 latency (ms): 2.94
p95 latency (ms): 5.88
p99 latency (ms): 6.21
```

## Interpreting Results

### Good Performance Indicators
- **notes/sec stays stable across runs**: indexing path changes are not regressing
- **p95 query latency stays stable**: retrieval changes are not regressing tail latency
- **recall@5 and mrr@10 stay stable or improve**: relevance quality is not regressing
- **sqlite size growth stays proportional to note volume**: vector/index growth remains predictable

### Areas for Improvement
- **indexing throughput drops significantly**: inspect per-note indexing and vector chunking
- **p95/p99 latency spikes**: inspect fusion and vector candidate scans
- **quality metrics drop**: inspect ranking fusion and chunking strategy
- **db size growth is disproportionate**: inspect chunk sizing and duplicated indexed text

## Tracking Improvements

Before making optimizations:
1. Run benchmarks to establish baseline
2. Optionally set `BASIC_MEMORY_BENCHMARK_OUTPUT` to capture machine-readable metrics
3. Save output for comparison
4. Note any particular pain points (e.g., slow search indexing)

After optimizations:
1. Run the same benchmarks
2. Compare metrics:
   - Notes/sec should increase for indexing and incremental re-index
   - p95/p99 query latency should decrease or remain stable
   - SQLite size should remain proportional to note volume
3. Optionally run with guardrail env vars in nightly CI to catch regressions
4. Document improvements in PR

## Guardrail Environment Variables

- `BASIC_MEMORY_BENCH_MIN_COLD_NOTES_PER_SEC`
- `BASIC_MEMORY_BENCH_MAX_COLD_SQLITE_SIZE_MB`
- `BASIC_MEMORY_BENCH_MIN_INCREMENTAL_NOTES_PER_SEC`
- `BASIC_MEMORY_BENCH_MAX_INCREMENTAL_SQLITE_SIZE_MB`
- `BASIC_MEMORY_BENCH_MAX_FTS_P95_MS`
- `BASIC_MEMORY_BENCH_MAX_FTS_P99_MS`
- `BASIC_MEMORY_BENCH_MAX_VECTOR_P95_MS`
- `BASIC_MEMORY_BENCH_MAX_VECTOR_P99_MS`
- `BASIC_MEMORY_BENCH_MAX_HYBRID_P95_MS`
- `BASIC_MEMORY_BENCH_MAX_HYBRID_P99_MS`
- `BASIC_MEMORY_BENCH_MIN_LEXICAL_FTS_RECALL_AT_5`
- `BASIC_MEMORY_BENCH_MIN_LEXICAL_FTS_MRR_AT_10`
- `BASIC_MEMORY_BENCH_MIN_LEXICAL_VECTOR_RECALL_AT_5`
- `BASIC_MEMORY_BENCH_MIN_LEXICAL_VECTOR_MRR_AT_10`
- `BASIC_MEMORY_BENCH_MIN_LEXICAL_HYBRID_RECALL_AT_5`
- `BASIC_MEMORY_BENCH_MIN_LEXICAL_HYBRID_MRR_AT_10`
- `BASIC_MEMORY_BENCH_MIN_PARAPHRASE_FTS_RECALL_AT_5`
- `BASIC_MEMORY_BENCH_MIN_PARAPHRASE_FTS_MRR_AT_10`
- `BASIC_MEMORY_BENCH_MIN_PARAPHRASE_VECTOR_RECALL_AT_5`
- `BASIC_MEMORY_BENCH_MIN_PARAPHRASE_VECTOR_MRR_AT_10`
- `BASIC_MEMORY_BENCH_MIN_PARAPHRASE_HYBRID_RECALL_AT_5`
- `BASIC_MEMORY_BENCH_MIN_PARAPHRASE_HYBRID_MRR_AT_10`

## Related Issues

- [#351: Performance: Optimize sync/indexing for cloud deployments](https://github.com/basicmachines-co/basic-memory/issues/351)

## Test File Generation

Benchmarks generate realistic markdown notes with:
- YAML frontmatter with tags
- Multiple markdown sections per note
- Repeated domain-specific terms for retrieval-mode comparisons
- Sufficient content length to exercise chunk-based semantic indexing
