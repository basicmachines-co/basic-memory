# AGENTS.md - basic-memory-benchmarks Guide

## Project Overview

`basic-memory-benchmarks` is a standalone benchmark harness for comparing Basic Memory against other memory systems.

Primary goals:
- Deterministic retrieval benchmarks
- Optional LLM-as-a-judge benchmarks
- Public, reproducible artifact publication (including provenance metadata)

This repo is intentionally isolated from `basic-memory` so benchmark dependencies do not pollute the product repo.

## Build / Test Commands

- Install: `uv sync --group dev`
- Install judge extras: `uv sync --group dev --extra judge`
- Run tests: `uv run pytest -q`
- Lint: `uv run ruff check .`
- Type check: `uv run pyright`

Recommended local gate before pushing:
1. `uv run pytest -q`
2. `uv run ruff check .`
3. `uv run pyright`

## Benchmark Commands

CLI entrypoint: `uv run bm-bench ...`

Dataset and conversion:
- `uv run bm-bench datasets fetch --dataset locomo`
- `uv run bm-bench convert locomo --dataset-path benchmarks/datasets/locomo/locomo10.json --output-dir benchmarks/generated/locomo`

Run retrieval:
- `uv run bm-bench run retrieval --providers bm-local,mem0-local --dataset-id locomo --dataset-path benchmarks/datasets/locomo/locomo10.json --corpus-dir benchmarks/generated/locomo/docs --queries-path benchmarks/generated/locomo/queries.json --output-root benchmarks/runs --allow-provider-skip`

Run judge (optional):
- `uv run bm-bench run judge --run-dir benchmarks/runs/<run-id>`

Validate and publish:
- `uv run bm-bench validate-artifacts --run-dir benchmarks/runs/<run-id>`
- `uv run bm-bench publish --run-dir benchmarks/runs/<run-id> --destination benchmarks/results/public`

`just` shortcuts:
- `just bench-smoke`
- `just bench-fetch-locomo`
- `just bench-convert-locomo`
- `just bench-run-bm-local`
- `just bench-run-mem0-local`
- `just bench-run-full`
- `just bench-judge RUN_DIR=benchmarks/runs/<run-id>`
- `just bench-publish RUN_DIR=benchmarks/runs/<run-id>`

## Repository Layout

- `src/basic_memory_benchmarks/cli.py` - CLI surface
- `src/basic_memory_benchmarks/runner.py` - run orchestration
- `src/basic_memory_benchmarks/providers/` - provider adapters (`bm-local`, `bm-cloud`, `mem0-local`, `zep-reference`)
- `src/basic_memory_benchmarks/scoring/` - retrieval + judge scoring
- `src/basic_memory_benchmarks/reporting/` - artifact writers / comparison helpers
- `src/basic_memory_benchmarks/converters/` - dataset conversion logic
- `src/basic_memory_benchmarks/datasets/` - dataset fetch/load helpers
- `benchmarks/datasets/` - source metadata + download helpers
- `benchmarks/generated/` - generated corpus/query outputs
- `benchmarks/runs/` - raw run artifacts
- `benchmarks/results/public/` - published bundles
- `tests/`, `test-int/` - unit and integration tests

## Benchmark Integrity Rules

These are non-negotiable for headline comparisons:

1. Use the same query set and same `top_k` across providers.
2. Do not apply provider-specific query rewriting for headline runs.
3. Keep official categories and adversarial breakout separate.
4. Record provider `SKIPPED(reason)` explicitly; do not silently drop providers.
5. Always capture provenance in `manifest.json`:
   - benchmark repo SHA
   - BM source + resolved BM SHA
   - provider versions
   - dataset source + checksum
   - runtime metadata

## Provider Notes

### Basic Memory (`bm-local`)

- Interact via external `bm` CLI contract, not internal imports from `basic-memory`.
- Repeated runs against the same corpus path may reuse an existing BM project name.
- The benchmark command is typically invoked through `uv run ...` so `.venv/bin/bm` is used.

### Mem0 (`mem0-local`)

- Requires `OPENAI_API_KEY` (or equivalent configured model creds).
- Ingest and search use a stable benchmark `user_id` namespace per run.
- Store source metadata (`source_doc_id`, `source_path`, `conversation_id`, `dataset_id`) for grounding.

## Environment / Secrets

- Keep secrets in `.env` (already gitignored).
- Avoid exporting unrelated `BASIC_MEMORY_*` environment variables into benchmark runs unless intended.
- Prefer setting only required credentials for run reproducibility.

## Dataset Policy

- If redistribution is allowed: publish snapshot + checksum.
- If restricted: publish canonical source link + downloader + checksum verification.
- Always publish conversion code and run artifacts.

## Coding Guidelines

- Python 3.12+ style with type hints.
- Keep diffs focused and minimal.
- Fail fast; do not silently swallow benchmark-critical failures.
- Use `apply_patch` for targeted edits when practical.
- Add tests for behavior changes in adapters, scoring, or artifact schemas.

## Git / Collaboration

- Use non-interactive git commands.
- Sign commits: `git commit -s`.
- Do not commit `.env`, generated runs, or local editor state.
- If changing benchmark behavior, include a brief note in PR/commit describing fairness or reproducibility impact.
