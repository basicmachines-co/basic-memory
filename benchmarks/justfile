# basic-memory-benchmarks command runner

set dotenv-load := true

# --- Paths and defaults ---

bm_local_path := env_var_or_default("BM_LOCAL_PATH", "")
bm_local_path_flag := if bm_local_path != "" { "--bm-local-path " + bm_local_path } else { "" }
locomo_dataset_path := "benchmarks/datasets/locomo/locomo10.json"
locomo_output_dir := "benchmarks/generated/locomo"
locomo_c1_output_dir := "benchmarks/generated/locomo-c1"
longmemeval_dataset_path := "benchmarks/datasets/longmemeval/longmemeval_s.json"
longmemeval_output_dir := "benchmarks/generated/longmemeval-s"
longmemeval_dev_output_dir := "benchmarks/generated/longmemeval-s-dev"

# --- Repo maintenance ---

sync:
    uv sync --group dev

sync-judge:
    uv sync --group dev --extra judge

test:
    uv run pytest -q

lint:
    uv run ruff check .

format:
    uv run ruff format .

typecheck:
    uv run pyright

check: lint typecheck test

# --- Dataset prep ---

bench-fetch-locomo:
    uv run bm-bench datasets fetch --dataset locomo --output {{locomo_dataset_path}}

bench-convert-locomo:
    uv run bm-bench convert locomo --dataset-path {{locomo_dataset_path}} --output-dir {{locomo_output_dir}}

bench-convert-locomo-c1:
    uv run bm-bench convert locomo --dataset-path {{locomo_dataset_path}} --output-dir {{locomo_c1_output_dir}} --max-conversations 1

bench-make-quick25:
    uv run python -c 'import json; from pathlib import Path; queries_path=Path("benchmarks/generated/locomo-c1/queries.json"); quick_path=Path("benchmarks/generated/locomo-c1/queries.quick25.json"); queries=json.loads(queries_path.read_text()); quick_path.write_text(json.dumps(queries[:25], indent=2)+"\n"); print(f"Wrote {len(queries[:25])} queries to {quick_path}")'

bench-prepare-short: bench-fetch-locomo bench-convert-locomo-c1 bench-make-quick25

bench-prepare-long: bench-fetch-locomo bench-convert-locomo

bench-fetch-longmemeval:
    uv run bm-bench datasets fetch --dataset longmemeval-s --output {{longmemeval_dataset_path}}

bench-convert-longmemeval:
    uv run bm-bench convert longmemeval --dataset-path {{longmemeval_dataset_path}} --output-dir {{longmemeval_output_dir}}

# Dev slice: first 25 questions for fast iteration
bench-convert-longmemeval-dev:
    uv run bm-bench convert longmemeval --dataset-path {{longmemeval_dataset_path}} --output-dir {{longmemeval_dev_output_dir}} --max-questions 25

bench-prepare-longmemeval: bench-fetch-longmemeval bench-convert-longmemeval

bench-fetch-convomem:
    uv run bm-bench datasets fetch --dataset convomem --context-sizes 10,30

bench-convert-convomem:
    uv run bm-bench convert convomem --sample-per-stratum 25 --seed 42

bench-prepare-convomem: bench-fetch-convomem bench-convert-convomem

bench-fetch-locomo-audit:
    uv run bm-bench datasets fetch --dataset locomo-audit

bench-convert-locomo-corrected: bench-fetch-locomo bench-fetch-locomo-audit
    uv run bm-bench convert locomo --dataset-path {{locomo_dataset_path}} --output-dir benchmarks/generated/locomo-corrected --audit-corrections benchmarks/datasets/locomo-audit/corrections.json

# Grouped retrieval over the LongMemEval-S dev slice (bm-local only)
bench-run-longmemeval-dev:
    uv run bm-bench run retrieval \
      --dataset-id longmemeval_s \
      --dataset-path {{longmemeval_dataset_path}} \
      --corpus-dir {{longmemeval_dev_output_dir}}/groups \
      --queries-path {{longmemeval_dev_output_dir}}/queries.json \
      --providers bm-local \
      {{bm_local_path_flag}} \
      --strict-providers

# Grouped retrieval over full LongMemEval-S (slow: 500 isolated group corpora)
bench-run-longmemeval:
    uv run bm-bench run retrieval \
      --dataset-id longmemeval_s \
      --dataset-path {{longmemeval_dataset_path}} \
      --corpus-dir {{longmemeval_output_dir}}/groups \
      --queries-path {{longmemeval_output_dir}}/queries.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --allow-provider-skip

# --- One-command pipelines ---

# Full retrieval benchmark pipeline:
# 1) sync deps, 2) fetch+convert long dataset, 3) run full retrieval
bench-full:
    just sync
    just bench-prepare-long
    just bench-run-full

# Full retrieval + judge pipeline:
# 1) sync deps (+judge extras), 2) fetch+convert long dataset, 3) run full with judge
bench-full-judge model="gpt-4o-mini":
    just sync-judge
    just bench-prepare-long
    just bench-run-full-judge model="{{model}}"

# --- Benchmark execution ---

bench-smoke:
    uv run bm-bench run retrieval \
      --dataset-id synthetic \
      --dataset-path benchmarks/synthetic/queries.json \
      --corpus-dir benchmarks/synthetic/docs \
      --queries-path benchmarks/synthetic/queries.json \
      --providers bm-local,mem0-local \
      --allow-provider-skip

# Short benchmark: one-conversation LoCoMo slice + 25-query quickset
bench-run-short:
    uv run bm-bench run retrieval \
      --dataset-id locomo-c1-quick25 \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo-c1/docs \
      --queries-path benchmarks/generated/locomo-c1/queries.quick25.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --allow-provider-skip

bench-run-short-strict:
    uv run bm-bench run retrieval \
      --dataset-id locomo-c1-quick25 \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo-c1/docs \
      --queries-path benchmarks/generated/locomo-c1/queries.quick25.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --strict-providers

# Long benchmark: full LoCoMo query set
bench-run-long:
    uv run bm-bench run retrieval \
      --dataset-id locomo \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo/docs \
      --queries-path benchmarks/generated/locomo/queries.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --allow-provider-skip

bench-run-long-strict:
    uv run bm-bench run retrieval \
      --dataset-id locomo \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo/docs \
      --queries-path benchmarks/generated/locomo/queries.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --strict-providers

bench-run-bm-local:
    uv run bm-bench run retrieval \
      --providers bm-local \
      --dataset-id locomo \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo/docs \
      --queries-path benchmarks/generated/locomo/queries.json \
      {{bm_local_path_flag}}

bench-run-mem0-local:
    uv run bm-bench run retrieval \
      --providers mem0-local \
      --dataset-id locomo \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo/docs \
      --queries-path benchmarks/generated/locomo/queries.json \
      --allow-provider-skip

bench-run-full:
    uv run bm-bench run full \
      --dataset-id locomo \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo/docs \
      --queries-path benchmarks/generated/locomo/queries.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --allow-provider-skip

bench-run-full-judge model="gpt-4o-mini":
    uv run bm-bench run full \
      --dataset-id locomo \
      --dataset-path {{locomo_dataset_path}} \
      --corpus-dir benchmarks/generated/locomo/docs \
      --queries-path benchmarks/generated/locomo/queries.json \
      --providers bm-local,mem0-local \
      {{bm_local_path_flag}} \
      --allow-provider-skip \
      --judge \
      --judge-model "{{model}}"

# --- Artifacts and comparison ---

bench-latest-run:
    #!/usr/bin/env bash
    set -euo pipefail
    ls -1dt benchmarks/runs/* | head -n 1

bench-judge run_dir model="gpt-4o-mini":
    uv run bm-bench run judge --run-dir "{{run_dir}}" --model "{{model}}"

bench-validate run_dir:
    uv run bm-bench validate-artifacts --run-dir "{{run_dir}}"

bench-publish run_dir destination="benchmarks/results/public":
    uv run bm-bench publish --run-dir "{{run_dir}}" --destination "{{destination}}"

bench-compare baseline candidate provider="bm-local" metric="recall_at_5":
    uv run bm-bench compare "{{baseline}}" "{{candidate}}" --provider "{{provider}}" --metric "{{metric}}"

default:
    @just --list
