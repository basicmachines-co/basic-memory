# Benchmark Runbook

This document is the canonical operator runbook for benchmark execution in
`basic-memory-benchmarks`.

It covers:
1. current benchmark workflows and commands,
2. current manual commit-to-commit comparison workflow,
3. planned (not yet implemented) revision matrix workflow.

## Current vs Planned

| Area | Status |
| --- | --- |
| Single run execution (`run retrieval`, `run full`, `run judge`) | Implemented |
| `just` one-command pipelines (`bench-full`, `bench-full-judge`) | Implemented |
| Artifact generation and publish/compare commands | Implemented |
| Manual BM revision comparison via worktrees + `--bm-local-path` | Implemented workflow, manual orchestration |
| `bm-bench run revision-matrix` | Planned, not implemented yet |

## 1) Purpose and Scope

### Goals

- deterministic retrieval evaluation for BM and comparator providers,
- reproducible latency and quality tracking over time,
- publishable artifacts with provenance.

### Headline scoring

- Official headline: LoCoMo categories 1-4 (`official_headline` in summaries)
- Adversarial breakout: LoCoMo category 5 (`adversarial_breakout`)

### Fairness contract

- Same query set for all providers in the same run.
- Same `top_k` for all providers in the same run.
- No provider-specific query rewriting for headline runs.
- Provider failures/skips must be explicit in artifacts (`provider-status.json`).

## 2) Prerequisites

### Repositories and paths

- benchmark repo: clone of `basicmachines-co/basic-memory-benchmarks`
- BM local repo: set `BM_LOCAL_PATH` env var (or in `.env`) to your local `basic-memory` checkout

### Environment

- `.env` is auto-loaded by `just` (`set dotenv-load := true`).
- For `mem0-local`, set `OPENAI_API_KEY`.

### One-time setup

```bash
cd /path/to/basic-memory-benchmarks
just sync
```

If you plan to run judge metrics:

```bash
just sync-judge
```

### Dataset assumptions

LoCoMo source and converted outputs are created by:

```bash
just bench-prepare-short
just bench-prepare-long
```

## 3) Command Surface (Current Source of Truth)

### `just` commands (current)

- `bench-full`
- `bench-full-judge`
- `bench-prepare-short`
- `bench-prepare-long`
- `bench-run-short`
- `bench-run-long`
- `bench-run-full`
- `bench-judge`
- `bench-validate`
- `bench-publish`
- `bench-compare`
- `bench-latest-run`

### `bm-bench` CLI (current)

Top-level commands:

- `datasets fetch`
- `convert locomo`
- `run retrieval`
- `run full`
- `run judge`
- `compare`
- `validate-artifacts`
- `publish`

## 4) How Runs Work Today (Operator Workflow)

### One-command full retrieval run

```bash
cd /path/to/basic-memory-benchmarks
just bench-full
```

This runs:
1. `just sync`
2. `just bench-prepare-long`
3. `just bench-run-full`

### One-command full retrieval + judge

```bash
cd /path/to/basic-memory-benchmarks
just bench-full-judge
```

This runs:
1. `just sync-judge`
2. `just bench-prepare-long`
3. `just bench-run-full-judge`

### Short vs long workflows

Short (quick25):

```bash
just bench-prepare-short
just bench-run-short
```

Long (full LoCoMo):

```bash
just bench-prepare-long
just bench-run-long
```

Strict provider mode (fail run if any provider fails/skips):

```bash
just bench-run-short-strict
just bench-run-long-strict
```

## 5) Run Lifecycle Internals (Current Behavior)

### `bm-local` provider flow

1. Resolve BM command:
   - default: `bm`
   - local override: `uv run --project <bm_local_path> basic-memory`
2. Create/reuse benchmark project:
   - `basic-memory project add bm-bench-<run_id> <corpus_dir>`
3. Reindex:
   - prefer `reindex --search --embeddings`
   - fallback to `reindex --search`
4. Wait for readiness:
   - if supported, poll `status --json --project <name> --local`
5. Start a warm MCP stdio session:
   - one long-lived `basic-memory mcp` process per provider run
6. Execute `search_notes` calls over MCP for each query.
7. Cleanup MCP session.

### `mem0-local` provider flow

1. Requires `OPENAI_API_KEY`.
2. Uses deterministic user namespace:
   - `bm-bench-<run_id>-mem0`
3. Ingests markdown corpus with metadata:
   - `source_doc_id`, `source_path`, `conversation_id`, `dataset_id`
4. Calls `Memory.search` for each query.
5. Cleans provider state via `delete_all(user_id=...)`.

## 6) Artifacts and Provenance

Each run writes to `benchmarks/runs/<run_id>/`.

Required files:

- `manifest.json`
- `provider-status.json`
- `per-query-retrieval.jsonl`
- `retrieval-summary.json`
- `summary.md`

Optional judge files:

- `per-query-judge.jsonl`
- `judge-summary.json`

### Key provenance fields

From `manifest.json`:

- `benchmark_git_sha`
- `bm_source`
- `bm_resolved_sha`
- `bm_local_path`
- `provider_versions`
- `dataset.checksum_sha256`

### Useful commands

Get latest run:

```bash
just bench-latest-run
```

Validate artifacts:

```bash
just bench-validate run_dir="$(just bench-latest-run)"
```

Publish bundle:

```bash
just bench-publish run_dir="$(just bench-latest-run)"
```

## 7) Commit-to-Commit Comparison (Current Manual Method)

Use this workflow today to compare BM revisions while keeping benchmark tooling fixed.

### Step 1: Create BM worktrees for target refs

```bash
BM_REPO=/path/to/basic-memory
WT_ROOT=/path/to/basic-memory-benchmarks/benchmarks/worktrees/basic-memory

mkdir -p "$WT_ROOT"

git -C "$BM_REPO" worktree add "$WT_ROOT/pre_fusion" f5a0e942^
git -C "$BM_REPO" worktree add "$WT_ROOT/fusion" f5a0e942
git -C "$BM_REPO" worktree add "$WT_ROOT/context_step1" f9b2a075
git -C "$BM_REPO" worktree add "$WT_ROOT/context_step2" 9331126b
git -C "$BM_REPO" worktree add "$WT_ROOT/current" HEAD
```

### Step 2: Prepare benchmark datasets once

```bash
cd /path/to/basic-memory-benchmarks
just sync
just bench-prepare-short
just bench-prepare-long
```

### Step 3: Run BM for each revision with deterministic run IDs

Run ID convention:

- short: `<revision>-short-r1`
- long: `<revision>-long-r1`

Example for one revision (`fusion`) and long dataset:

```bash
uv run bm-bench run retrieval \
  --run-id fusion-long-r1 \
  --dataset-id locomo \
  --dataset-path benchmarks/datasets/locomo/locomo10.json \
  --corpus-dir benchmarks/generated/locomo/docs \
  --queries-path benchmarks/generated/locomo/queries.json \
  --providers bm-local \
  --bm-local-path "$WT_ROOT/fusion" \
  --strict-providers
```

Example for one revision (`fusion`) and short dataset:

```bash
uv run bm-bench run retrieval \
  --run-id fusion-short-r1 \
  --dataset-id locomo-c1-quick25 \
  --dataset-path benchmarks/datasets/locomo/locomo10.json \
  --corpus-dir benchmarks/generated/locomo-c1/docs \
  --queries-path benchmarks/generated/locomo-c1/queries.quick25.json \
  --providers bm-local \
  --bm-local-path "$WT_ROOT/fusion" \
  --strict-providers
```

Repeat for:

- `pre_fusion` (`f5a0e942^`)
- `fusion` (`f5a0e942`)
- `context_step1` (`f9b2a075`)
- `context_step2` (`9331126b`)
- `current` (`HEAD`)

### Step 4: Run mem0 anchor once per dataset (optional but recommended)

Long anchor:

```bash
uv run bm-bench run retrieval \
  --run-id mem0-anchor-long-r1 \
  --dataset-id locomo \
  --dataset-path benchmarks/datasets/locomo/locomo10.json \
  --corpus-dir benchmarks/generated/locomo/docs \
  --queries-path benchmarks/generated/locomo/queries.json \
  --providers mem0-local \
  --allow-provider-skip
```

Short anchor:

```bash
uv run bm-bench run retrieval \
  --run-id mem0-anchor-short-r1 \
  --dataset-id locomo-c1-quick25 \
  --dataset-path benchmarks/datasets/locomo/locomo10.json \
  --corpus-dir benchmarks/generated/locomo-c1/docs \
  --queries-path benchmarks/generated/locomo-c1/queries.quick25.json \
  --providers mem0-local \
  --allow-provider-skip
```

### Step 5: Compare runs

```bash
just bench-compare \
  "benchmarks/runs/pre_fusion-long-r1/retrieval-summary.json" \
  "benchmarks/runs/fusion-long-r1/retrieval-summary.json" \
  bm-local \
  recall_at_5
```

Recommended metrics to compare:

- `recall_at_5`
- `recall_at_10`
- `mrr`
- `mean_latency_ms`
- `p95_latency_ms`

### Step 6: Record matrix results

Use a summary table with baseline deltas, for example:

| Revision | Dataset | Recall@5 | Recall@10 | MRR | Delta R@5 vs pre_fusion | Delta MRR vs pre_fusion |
| --- | --- | --- | --- | --- | --- | --- |
| pre_fusion | long | ... | ... | ... | 0.000 | 0.000 |
| fusion | long | ... | ... | ... | ... | ... |
| context_step1 | long | ... | ... | ... | ... | ... |
| context_step2 | long | ... | ... | ... | ... | ... |
| current | long | ... | ... | ... | ... | ... |

## 8) Planned Workflow: `run revision-matrix` (Not Implemented Yet)

Status: planned.

Planned defaults:

- worktree-based BM revision execution,
- parallel workers: `2`,
- datasets: `both` (short + long),
- replicates: `1`,
- BM per revision + fixed mem0 anchor.

Planned output root:

- `benchmarks/matrices/<matrix_id>/`

Planned command shape:

```bash
uv run bm-bench run revision-matrix \
  --bm-repo-path /path/to/basic-memory \
  --revisions pre_fusion=f5a0e942^ \
  --revisions fusion=f5a0e942 \
  --revisions context_step1=f9b2a075 \
  --revisions context_step2=9331126b \
  --revisions current=HEAD \
  --baseline pre_fusion \
  --datasets both \
  --workers 2 \
  --replicates 1 \
  --providers-mode bm-only-mem0-anchor
```

## 9) Troubleshooting

### `bm-local` fails on `project add`

Symptoms:

- `provider-status.json` shows `bm-local` state `error`
- reason contains `basic-memory project add ... returned non-zero exit status`

Checks:

1. verify path exists and is a BM repo:
   - `ls /path/to/basic-memory/pyproject.toml`
2. verify command works directly:
   - `uv run --project /path/to/basic-memory basic-memory --version`
3. rerun with explicit local path:
   - `--bm-local-path /path/to/basic-memory`
4. use strict mode while debugging:
   - `--strict-providers`

### `status --json` behavior differs by BM build

- Some BM environments support `status --json`; some older ones do not.
- Provider auto-detects support.
- If unsupported, benchmark still runs after reindex without JSON readiness polling.

### Provider `skipped` vs `error`

- `skipped`: expected gate not met (for example missing `OPENAI_API_KEY` for mem0).
- `error`: provider attempted execution and failed.

### Long run duration

- Full LoCoMo runs are expected to take significantly longer than quick25 runs.
- Use `bench-run-short` for quick checks before full runs.

### Rerun single provider / single revision

BM-only rerun with explicit revision worktree:

```bash
uv run bm-bench run retrieval \
  --providers bm-local \
  --bm-local-path "$WT_ROOT/fusion" \
  --run-id fusion-long-r1-retry \
  --dataset-id locomo \
  --dataset-path benchmarks/datasets/locomo/locomo10.json \
  --corpus-dir benchmarks/generated/locomo/docs \
  --queries-path benchmarks/generated/locomo/queries.json
```

## 10) FAQ

### Why use worktrees if we already use `uv`?

`uv` solves environment/dependency reproducibility. Worktrees solve source isolation. For commit-to-commit benchmarking, worktrees make each revision explicit, auditable, and safe to run in parallel.

### When should I use strict providers?

Use strict mode (`--strict-providers`) for regression investigations and CI gates where silent skips are unacceptable. Use allow-skip mode for exploratory local runs where external credentials may be missing.

### How do I publish run bundles?

```bash
just bench-publish run_dir="$(just bench-latest-run)"
```

Or target a specific run directory:

```bash
just bench-publish run_dir="benchmarks/runs/<run_id>"
```

## 11) Validation Checklist for This Runbook

Command surface checks:

```bash
just --list
uv run bm-bench --help
uv run bm-bench run --help
```

Dry-run checks:

```bash
just --dry-run bench-run-short
just --dry-run bench-run-long
just --dry-run bench-full
just --dry-run bench-full-judge
```

Artifact field checks:

```bash
latest=$(just bench-latest-run)
cat "$latest/manifest.json"
cat "$latest/provider-status.json"
```

Comparison check:

```bash
just bench-compare \
  "benchmarks/runs/<baseline_run>/retrieval-summary.json" \
  "benchmarks/runs/<candidate_run>/retrieval-summary.json" \
  bm-local \
  recall_at_5
```
