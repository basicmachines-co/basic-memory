# basic-memory-benchmarks

Standalone, reproducible benchmark suite for comparing Basic Memory against competitor memory systems.

## Goals

- Deterministic retrieval benchmarks (Recall@5/10, MRR, Precision@5, content-hit, latency)
- Optional LLM-as-judge scoring (Pydantic Evals)
- Public artifacts with provenance and reproducibility metadata
- Clean dependency isolation from the core `basic-memory` repository

## Current v1 Scope

- Providers:
  - `bm-local` (warm `bm mcp` stdio session)
  - `bm-cloud` (optional, credential-gated)
  - `mem0-local`
  - `zep-reference` (reference-only in v1)
- Datasets:
  - LoCoMo (primary)
  - LongMemEval scaffold (placeholder)
  - Built-in synthetic smoke corpus

## Installation

```bash
uv sync --group dev
```

Optional judge dependencies:

```bash
uv sync --group dev --extra judge
```

## Quickstart

### 1) Fetch LoCoMo dataset

```bash
uv run bm-bench datasets fetch --dataset locomo
```

### 2) Convert LoCoMo into benchmark corpus

```bash
uv run bm-bench convert locomo
```

### 3) Run retrieval benchmark

```bash
uv run bm-bench run retrieval \
  --providers bm-local,mem0-local \
  --corpus-dir benchmarks/generated/locomo/docs \
  --queries-path benchmarks/generated/locomo/queries.json
```

### 4) End-to-end QA scoring

Generates an answer per query from each provider's retrieved context, then
grades it against the expected answer with an LLM judge. This is the stage that
produces benchmark-comparable accuracy numbers; retrieval metrics alone measure
only the search layer.

```bash
uv run bm-bench run qa --run-dir benchmarks/runs/<run-id> \
  --answerer claude:claude-haiku-4-5 \
  --judge claude:claude-sonnet-4-6
```

Runner specs select the transport:

- `claude:<model>` — Claude Code CLI in print mode. Bills the operator's Claude
  subscription plan; no API key needed. Requires `claude` on PATH.
- `openai-compat:<model>@<base_url>` — any OpenAI-compatible endpoint (Ollama,
  LM Studio, vLLM, OpenAI). Set `OPENAI_API_KEY` if the endpoint requires auth.

The same answerer and judge are used for every provider in the run, so
cross-provider comparisons hold the model constant. Answer and judge prompts
are fixed in `scoring/qa.py`; the answerer is instructed to abstain ("I don't
know") when the retrieved memories don't contain the answer, and abstention is
graded correct only when the gold answer marks the question unanswerable
(LoCoMo adversarial cases).

### 5) Optional retrieval-context judge (legacy)

Scores whether the *retrieved context* contains the expected answer, without
answer generation:

```bash
uv run bm-bench run judge --run-dir benchmarks/runs/<run-id>
```

### 6) Publish run artifacts

```bash
uv run bm-bench publish --run-dir benchmarks/runs/<run-id>
```

## LoCoMo corrected answer key

The April 2026 Penfield Labs audit ([locomo-audit](https://github.com/dial481/locomo-audit))
found 156 answer-key errors in LoCoMo's 1,540 usable questions — hallucinated
facts, temporal arithmetic mistakes, attribution errors, and wrong evidence
citations. Published LoCoMo numbers should score against the corrected key and
say so.

```bash
uv run bm-bench datasets fetch --dataset locomo-audit   # pinned audit revision
uv run bm-bench convert locomo \
  --audit-corrections benchmarks/datasets/locomo-audit/corrections.json \
  --output-dir benchmarks/generated/locomo-corrected
```

Corrected queries replace both the expected answer (for QA scoring) and the
evidence citations (for retrieval ground truth), and carry
`audit_corrected: true` + the audit's error type in metadata. Every correction
is cross-checked against the dataset's question text at conversion time, so
audit/dataset drift fails loudly. The adversarial category remains excluded
from headline metrics per the LoCoMo protocol.

## LongMemEval-S

LongMemEval (Wu et al., ICLR 2025) gives each of its 500 questions an
independent haystack of ~50 chat sessions, so it runs in **grouped mode**: the
converter writes one corpus per question under `groups/<question_id>/docs`,
and the runner ingests + queries each group in isolation (fresh provider
instance, group-suffixed run id namespacing the BM project / mem0 user).

```bash
just bench-prepare-longmemeval        # fetch (~278MB) + convert all 500
just bench-convert-longmemeval-dev    # or: 25-question dev slice
just bench-run-longmemeval-dev        # grouped retrieval, bm-local
```

Then score answers with the QA stage as usual (`run qa --run-dir ...`). The
question's ask-date is carried in query metadata and appended to the question
for both the answerer and the judge — temporal-reasoning questions are
unanswerable without it.

Anti-leakage: the raw dataset marks evidence sessions via an `answer_`
session-id prefix and per-turn `has_answer` flags. The converter remaps all
session ids to neutral positional ids (`<qid>-s012`) and drops turn flags, so
ingested corpora carry no evidence markers.

## ConvoMem (sampled)

ConvoMem (Salesforce, Apache-2.0) ships ~75K QA pairs as pre-mixed test cases
— each a self-contained haystack of conversations plus questions — which map
1:1 onto grouped mode. The full dataset is multi-GB, so fetching is selective:
batch files are indexed with cheap HTTP Range tail-probes and only files
matching the requested context sizes are downloaded. The probe index
(`index.json`) records every file including the ones not downloaded, so the
selection is auditable.

```bash
uv run bm-bench datasets fetch --dataset convomem --context-sizes 10,30
uv run bm-bench convert convomem --sample-per-stratum 25 --seed 42
```

Sampling is stratified by (category, contextSize) with a fixed seed;
`sampling.json` records the seed, per-stratum population, and sample counts —
a published number states exactly which slice of ConvoMem it covers. Note
`--sample-per-stratum` counts *cases* (haystacks); larger-context cases carry
multiple questions each, all sharing one ingested group corpus.

Anti-leakage: raw conversations carry `containsEvidence`/`model_name` fields;
rendered docs include neither and conversation ids are remapped to neutral
positional ids.

## Basic Memory source policy

By default this project tracks Basic Memory from `main`.

Each run manifest stores:
- BM source (`github main` or local path override)
- resolved BM commit SHA

Local override:

```bash
uv run bm-bench run retrieval \
  --bm-local-path /path/to/basic-memory
```

## Mem0 local requirements

`mem0-local` needs a model backend, picked in priority order:

**1. Local OpenAI-compatible endpoint (zero API spend, e.g. Ollama):**

```bash
export MEM0_OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1
# optional overrides (defaults shown):
# MEM0_LLM_MODEL=qwen2.5:3b  MEM0_EMBED_MODEL=nomic-embed-text  MEM0_EMBED_DIMS=768
# MEM0_OPENAI_COMPAT_API_KEY=local  MEM0_QDRANT_PATH=benchmarks/.mem0-qdrant
```

LLM and embeddings both route to the endpoint; the qdrant store is created
per-run with matching dimensions under `MEM0_QDRANT_PATH`.

**2. OpenAI (mem0's defaults):** `export OPENAI_API_KEY=...`

With neither set, provider status is recorded as `SKIPPED(reason)`.

`MEM0_INFER=true` enables mem0's LLM fact-extraction at ingest (its
recommended/headline mode); default is `false` (raw add), which matches the
existing baselines. The active backend, models, and infer mode are recorded in
the run manifest's provider metadata.

## supermemory local requirements

`supermemory-local` targets a running [supermemory-server](https://github.com/supermemoryai/supermemory)
(self-hosted binary; pin v0.0.2). The provider does not manage the server —
start it yourself and export:

```bash
export SUPERMEMORY_API_KEY=sm_...          # printed on the server's first boot
# optional:
# SUPERMEMORY_BASE_URL=http://localhost:6767
# SUPERMEMORY_INGEST_TIMEOUT_S=900
# SUPERMEMORY_SERVER_VERSION=0.0.2        # recorded in the run manifest
```

Server-side notes for fair runs: start from a **fresh** `SUPERMEMORY_DATA_DIR`
per benchmark (upstream issue #1103: upgraded stores return empty searches),
and configure its LLM via `OPENAI_BASE_URL`/`OPENAI_MODEL`. Ingestion is async
(queued → ... → done|failed); the provider polls every document to a terminal
state before searching and fails the run loudly on any failed document rather
than silently scoring partial ingestion. Scoping is one container tag per run
id; cleanup bulk-deletes the container.

Without `SUPERMEMORY_API_KEY` or with the server unreachable, provider status
is recorded as `SKIPPED(reason)`.

## BM indexing readiness

`bm-local` verifies index readiness before querying.

- If the installed `bm` supports `bm status --json`, readiness is polled from that output.
- If `--json` is not available in the installed `bm`, the benchmark proceeds after reindex.

## Run Artifacts

Per run (`benchmarks/runs/<run-id>/`):

- `manifest.json`
- `provider-status.json`
- `per-query-retrieval.jsonl`
- `retrieval-summary.json`
- `per-query-qa.jsonl` (optional)
- `qa-summary.json` (optional)
- `per-query-judge.jsonl` (optional)
- `judge-summary.json` (optional)
- `summary.md`

## Just commands

```bash
just bench-smoke
just bench-fetch-locomo
just bench-convert-locomo
just bench-run-bm-local
just bench-run-mem0-local
just bench-run-full
just bench-judge
just bench-publish RUN_DIR=benchmarks/runs/<run-id>
```

## Notes on dataset publication

Dataset publication follows licensing constraints:
- If redistribution is permitted: snapshot + checksum may be published.
- If not: canonical source links + downloader + checksum verification are published.
