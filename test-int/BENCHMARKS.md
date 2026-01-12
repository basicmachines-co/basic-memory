# Performance Benchmarks

This directory contains performance benchmark tests for Basic Memory's sync/indexing operations.

## Purpose

These benchmarks measure baseline performance to track improvements from optimizations. They are particularly important for:
- Cloud deployments with ephemeral databases that need fast re-indexing
- Large repositories (100s to 1000s of files)
- Validating optimization efforts

## Running Benchmarks

### Run all benchmarks (excluding slow ones)
```bash
pytest test-int/test_sync_performance_benchmark.py -v -m "benchmark and not slow"
```

### Run specific benchmark
```bash
# 100 files (fast, ~10-30 seconds)
pytest test-int/test_sync_performance_benchmark.py::test_benchmark_sync_100_files -v

# 500 files (medium, ~1-3 minutes)
pytest test-int/test_sync_performance_benchmark.py::test_benchmark_sync_500_files -v

# 1000 files (slow, ~3-10 minutes)
pytest test-int/test_sync_performance_benchmark.py::test_benchmark_sync_1000_files -v

# Re-sync with no changes (tests scan performance)
pytest test-int/test_sync_performance_benchmark.py::test_benchmark_resync_no_changes -v
```

### Run all benchmarks including slow ones
```bash
pytest test-int/test_sync_performance_benchmark.py -v -m benchmark
```

### Skip benchmarks in regular test runs
```bash
pytest -m "not benchmark"
```

## Benchmark Output

Each benchmark provides detailed metrics including:

- **Performance Metrics**:
  - Total sync time
  - Files processed per second
  - Milliseconds per file

- **Database Metrics**:
  - Initial database size
  - Final database size
  - Database growth (total and per file)

- **Operation Counts**:
  - New files indexed
  - Modified files processed
  - Deleted files handled
  - Moved files tracked

## Example Output

```
======================================================================
BENCHMARK: Sync 100 files (small repository)
======================================================================

Generating 100 test files...
  Created files 0-100 (100/100)
  File generation completed in 0.15s (666.7 files/sec)

Initial database size: 120.00 KB

Starting sync of 100 files...

----------------------------------------------------------------------
RESULTS:
----------------------------------------------------------------------
Files processed:      100
  New:                100
  Modified:           0
  Deleted:            0
  Moved:              0

Performance:
  Total time:         12.34s
  Files/sec:          8.1
  ms/file:            123.4

Database:
  Initial size:       120.00 KB
  Final size:         5.23 MB
  Growth:             5.11 MB
  Growth per file:    52.31 KB
======================================================================
```

## Interpreting Results

### Good Performance Indicators
- **Files/sec > 10**: Good indexing speed for small-medium repos
- **Files/sec > 5**: Acceptable for large repos with complex relations
- **DB growth < 100KB per file**: Reasonable index size

### Areas for Improvement
- **Files/sec < 5**: May benefit from batch operations
- **ms/file > 200**: High latency per file, check for N+1 queries
- **DB growth > 200KB per file**: Search index may be bloated (trigrams?)

## Tracking Improvements

Before making optimizations:
1. Run benchmarks to establish baseline
2. Save output for comparison
3. Note any particular pain points (e.g., slow search indexing)

After optimizations:
1. Run the same benchmarks
2. Compare metrics:
   - Files/sec should increase
   - ms/file should decrease
   - DB growth per file may decrease (with search optimizations)
3. Document improvements in PR

## Related Issues

- [#351: Performance: Optimize sync/indexing for cloud deployments](https://github.com/basicmachines-co/basic-memory/issues/351)

## Test File Generation

Benchmarks generate realistic markdown files with:
- YAML frontmatter with tags
- 3-10 observations per file with categories
- 1-3 relations per file (including forward references)
- Varying content to simulate real usage
- Files organized in category subdirectories
