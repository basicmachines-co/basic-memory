# Benchmark Runbook

This document is the canonical operator runbook for benchmark execution in
the Core repository's `/benchmarks` package.

It covers:
1. current benchmark workflows and commands,
2. current manual commit-to-commit comparison workflow,
3. planned (not yet implemented) revision matrix workflow.

## Current vs Planned

| Area | Status |
| --- | --- |
| Single run execution (`run retrieval`, `run full`, `run qa`) | Implemented |
| Concurrent write convergence (`run concurrent-write`) | Implemented |
| `just` retrieval and QA workflows (`bench-full`, `bench-qa`) | Implemented |
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
- BEAM datasets reuse the same two containers with a dataset-keyed split:
  the nine answerable abilities are the headline and abstention is the
  breakout (abstention rows have empty ground truth, so their recall is 0 by
  construction). The field names are kept for artifact-schema stability and
  read as generic headline/breakout.

### Fairness contract

- Same query set for all providers in the same run.
- Same `top_k` for all providers in the same run.
- No provider-specific query rewriting for headline runs.
- Provider failures/skips must be explicit in artifacts (`provider-status.json`).

## 2) Prerequisites

### Repositories and paths

- benchmark package: `/benchmarks` in a clone of `basicmachines-co/basic-memory`
- BM local repo: set `BM_LOCAL_PATH` env var (or in `.env`) to the Core checkout under test

### Environment

- `.env` is auto-loaded by `just` (`set dotenv-load := true`).
- For `mem0-local`, set `OPENAI_API_KEY`.

### One-time setup

```bash
cd /path/to/basic-memory/benchmarks
just sync
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
- `bench-qa`
- `bench-concurrent-write-smoke`
- `bench-concurrent-write-load`
- `bench-prepare-short`
- `bench-prepare-long`
- `bench-prepare-beam`
- `bench-run-short`
- `bench-run-long`
- `bench-run-full`
- `bench-run-beam-100k`
- `bench-beam-score`
- `bench-validate`
- `bench-publish`
- `bench-compare`
- `bench-latest-run`

### `bm-bench` CLI (current)

Top-level commands:

- `datasets fetch`
- `convert locomo`
- `convert beam`
- `run retrieval`
- `run concurrent-write`
- `run full`
- `run qa`
- `run beam-score`
- `run rejudge`
- `run review`
- `compare`
- `validate-artifacts`
- `publish`

## 4) How Runs Work Today (Operator Workflow)

### One-command full retrieval run

```bash
cd /path/to/basic-memory/benchmarks
just bench-full
```

This runs:
1. `just sync`
2. `just bench-prepare-long`
3. `just bench-run-full`

### End-to-end QA scoring

```bash
cd /path/to/basic-memory/benchmarks
just bench-qa benchmarks/runs/<run_id>
```

This generates answers from each provider's retrieved context, applies the
same judge to every provider, and writes QA artifacts into the retrieval run.

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

Optional QA files:

- `per-query-qa.jsonl`
- `qa-summary.json`
- `per-query-qa-rejudge.jsonl`
- `qa-rejudge-summary.json`
- `qa-rejudge-flips.json`
- `review.html`
- `qa-diagnosis.json`

Optional BEAM files (only on BEAM runs, after `run beam-score`):

- `per-query-beam.jsonl`
- `beam-summary.json`
- `beam-summary.md`

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

## 6b) BEAM (arXiv 2510.27246)

BEAM probes ten long-term-memory abilities (abstention, contradiction
resolution, event ordering, information extraction, instruction following,
knowledge update, multi-session reasoning, preference following,
summarization, temporal reasoning) over synthetic multi-month conversations.
Each conversation is an isolated haystack, so BEAM runs in grouped mode.

### Workflow

```bash
just bench-fetch-beam            # sparse clone of chats/100K + chats/500K (never vendored)
just bench-convert-beam-100k     # -> benchmarks/generated/beam-100k/{groups,queries.json,conversion.json}
just bench-run-beam-100k         # grouped retrieval, unchanged runner/fairness surface
just bench-qa benchmarks/runs/<run-id>          # answers via the shared fixed QA stage
just bench-beam-score benchmarks/runs/<run-id>  # BEAM nugget/ordering scoring (post-hoc)
```

Runs pass `benchmarks/generated/beam-<tier>/conversion.json` as
`--dataset-path`: the converter records per-file sha256 of every consumed
chat/probing file there, so the run manifest's dataset checksum pins the exact
converted inputs. Tiers 100K/500K/1M share a layout and are supported; the
10M tier's combined plan-N layout is rejected in v1.

### Scoring definition

- Every probe's reference answer is pre-decomposed upstream into atomic
  nuggets (`rubric`). The judge scores each nugget 0/0.5/1 against the stored
  generated answer; the per-question nugget score is the mean.
- Event Ordering: the answer's lines are aligned against the ordered
  reference events with an LLM equivalence judge (greedy first-match, matched
  lines replaced by the reference label), then scored with set P/R/F1 and
  Kendall tau-b over union rank vectors; `tau_norm = (tau_b + 1) / 2` and
  `final_score = tau_norm * f1` are both recorded.
- Per-ability headline: mean `tau_norm` for event_ordering, mean nugget score
  for the other nine — mirroring upstream `report_results.py`. Reports always
  show all ten abilities plus per-answer token accounting; the macro average
  is reported beside them, never instead of them.
- Errored cases (answerer/judge failure, malformed verdict) are excluded from
  means and counted explicitly — never silently zero-scored.

### Runner provenance

Adapted from `mohammadtavakoli78/BEAM` (MIT code / CC BY-SA 4.0 data):

- `unified_llm_judge_base_prompt` (`src/prompts.py:11547`) →
  `BEAM_NUGGET_JUDGE_PROMPT` in `scoring/beam.py`. Modification: the probing
  question is injected as an explicit input — upstream's RESPONSIVENESS
  section references "the QUESTION" without ever providing it.
- `llm_equivalence` + `align_with_llm` + `event_ordering_score`
  (`src/evaluation/compute_metrics.py`) → equivalence prompt merged to
  single-prompt form for the package `LLMRunner`; the math (union ranks,
  tie rank, tau-b, normalisation, `final = tau_norm * f1`) is replicated,
  with one deviation: when the tie-corrected denominator is 0 (e.g. a
  single-event probe), upstream propagates scipy's NaN into its means,
  while this port maps undefined tau to `tau_norm = 0.0` so the case stays
  countable. System list = the response split on newlines (the shipped
  path; upstream's commented-out fact-extraction variant is not adopted),
  with blank lines dropped. Caveat: the package's fixed answer prompt asks
  for concise answers, which can yield single-line orderings that collapse
  to one system item and deflate F1/tau for every provider alike; a
  per-ability format hint would breach the fixed-prompt fairness contract,
  so v1 accepts and discloses this instead.
- Aggregation from `src/evaluation/report_results.py` as described above.
- Not reused, and why: `scipy.stats.kendalltau` (pure-Python tau-b instead —
  rank vectors are tiny); `json_repair` (silent repair conflicts with the
  package's fail-fast rule; malformed verdicts become explicit per-case
  errors); upstream's LangChain gpt-4.1-mini judge (replaced by the package
  `LLMRunner` seam — the judge is an operator flag recorded in artifacts, so
  published numbers must note the judge differs from the paper's); BEAM's
  answering baselines and RAG prompt (answers go through this package's fixed
  `ANSWER_PROMPT_TEMPLATE` + context budget so all providers face identical
  conditions — absolute numbers are therefore not directly comparable to the
  paper's tables); the HF materialization script and `chat.pickle` (the
  repo's JSON layout is read directly); the 10M tier.

## 6c) Agent-task eval (rich vs POSIX tool surfaces, issue #1401)

An AGENT-IN-THE-LOOP run kind: the same model, budget, task set, and seeded
corpus, with the TOOL SURFACE as the provider axis — `rich` (today's MCP
tools) vs `posix` (the `cat`/`grep`/`ls`/`find`/`tail`/`man` read surface from
#1399/#1406). The model proposes tool calls; the harness dispatches them
against an ephemeral per-surface `bm mcp` stdio instance, feeds results back,
and grades the final answer plus the resulting project state.

### Headline: tokens per completed task (xAFS framing)

The question is not "which surface is more accurate" but "what does a
completed memory task COST through each surface". The headline is
`(total agent tokens over ALL attempted tasks) / (tasks completed)` — `n/a`
(never 0) when nothing completed. Accuracy (pass rate), tool-call count,
turns, and wall time are always reported alongside; the report renders them
in one table so accuracy can never appear alone. Judge tokens (if a judge is
configured) are accounted separately and never enter the headline.

### Tasks and grading

Twelve declarative tasks (`agent_tasks/tasks.py`) derived from the shipped
skills — memory-continue (resume SPEC-9, recency window, two-hop chain),
memory-curate (find orphans, connect an orphan), memory-metadata-search
(status+priority, `$gt` boundary, nested review field), memory-tasks (create,
resume, complete) — plus SPEC-47's manual chain (search the man3/ pages, read
the right section). Each runs against a fresh copy of the hand-written seed
corpus (`benchmarks/datasets/agent-tasks/corpus`, ~25 notes with realistic
frontmatter/observations/relations, three orphans, three manpage-typed
notes); file mtimes are aged deterministically so the recency task has a
stable gold set. All twelve grade deterministically (planted `BMEVAL-*`
markers, fenced-JSON answer sets, frontmatter/observation/relation-row
predicates against the settled project and the run's SQLite index); a
`judge_rubric` grader type exists through the package judge seam but no v1
task uses it. Errored tasks (model transport, dead MCP session) are recorded
explicitly and excluded from means — never zero-scored, never dropped — and
keep the partial token/turn accounting already spent, so the cost columns and
`per-turn.jsonl` never under-report real model calls.

### Fairness contract additions

Same tasks, same model spec, same budgets (`--max-turns`, `--max-total-tokens`,
`--task-timeout`; the wall clock is also re-checked between tool dispatches so
one many-call assistant turn cannot overrun it), same corpus snapshot
(checksummed into the manifest), one
fixed prompt preamble, a fixed tool-result truncation cap
(`TOOL_RESULT_MAX_CHARS`), and deterministic tool-schema ordering (the
surface allowlist order). Only the surface definition varies, and both
definitions — config overrides, allowlist, and the tool list actually
observed from the server — are echoed into `manifest.json` as the audit
trail. `validate_surface_fairness` warns when surfaces attempted different
task sets. Note one structural asymmetry, by design: posix v1 (#1399) is
read-side only, so both surfaces share the rich write verbs (`write_note`,
`edit_note`, `move_note`, `delete_note`) and the A/B isolates the read-side
surface.

### POSIX surface gating (important)

This branch does NOT contain the posix tools — they live on the 1399/1403
stack. The surface is data (`agent_tasks/surfaces.py`): selecting it applies
`BASIC_MEMORY_ENABLE_POSIX_TOOLS=true` to the ephemeral BM env and requires
the six read tools in the server's advertised tool list. Because an older
BM silently ignores the unknown env var, the tool list is the authoritative
check: a missing tool raises `SurfaceUnavailableError` naming the missing
tools, the flag, and the branch requirement. Under the default
`--allow-surface-skip` the surface is recorded as `skipped` in
`surface-status.json` and the run continues; `--strict-surfaces` aborts.
Point `--bm-local-path` at a checkout with the flag for live posix runs; the
six tool names in `surfaces.py` are the single place to reconcile if names
drift on that branch.

### Model transports

The agent under test speaks `openai-compat:<model>@<base_url>` (any
`/chat/completions` endpoint implementing the `tools` parameter — Ollama,
vLLM, LM Studio, OpenAI, or an Anthropic model behind a LiteLLM proxy) or
`scripted:<path.json>` (canned turns for offline tests/smoke). An
`openai-compat` response must carry a `usage` block: omitting it would
silently zero the headline token accounting and disarm the tokens budget, so
it raises an explicit task error instead.
`claude:<model>` is rejected for the agent under test: `claude -p` runs its
own loop and cannot hand a `tool_use` block back to the harness — noted as
future work (Claude Agent SDK or an MCP-proxy recorder). The optional
`--judge` still accepts `claude:<model>` through the existing judge seam.

### Workflow

```bash
BM_LOCAL_PATH=.. just bench-agent-smoke     # LLM-free: scripted model, rich surface, strict
BM_LOCAL_PATH=.. just bench-agent-tasks     # rich,posix A/B with a local openai-compat model
uv run bm-bench run agent-tasks --surfaces rich,posix \
  --model openai-compat:qwen3@http://localhost:11434/v1 \
  --bm-local-path <bm-checkout>
```

The smoke script deliberately "knows" the curate-orphans answer: it proves
harness plumbing (session, dispatch, settle, grading, artifacts), not model
quality.

### Artifacts

`benchmarks/runs/<run-id>/`: `manifest.json` (BM SHA/version, model + judge
specs, budget, corpus checksum + file count, full surface echoes),
`surface-status.json` (ok/skipped/error per surface, explicit reasons),
`per-turn.jsonl` (per model turn and tool dispatch: tokens, tool name,
latency, result size), `per-task-agent.jsonl`, `agent-tasks-summary.json`,
`summary.md` (which repeats any skipped surface so the report cannot be
misread as a completed A/B). `validate-artifacts` remains retrieval-only for now (as for
concurrent-write); a kind-aware variant is a follow-up.

## 7) Commit-to-Commit Comparison (Current Manual Method)

Use this workflow today to compare BM revisions while keeping benchmark tooling fixed.

### Step 1: Create BM worktrees for target refs

```bash
BM_REPO=/path/to/basic-memory
WT_ROOT=/path/to/basic-memory/benchmarks/benchmarks/worktrees/basic-memory

mkdir -p "$WT_ROOT"

git -C "$BM_REPO" worktree add "$WT_ROOT/pre_fusion" f5a0e942^
git -C "$BM_REPO" worktree add "$WT_ROOT/fusion" f5a0e942
git -C "$BM_REPO" worktree add "$WT_ROOT/context_step1" f9b2a075
git -C "$BM_REPO" worktree add "$WT_ROOT/context_step2" 9331126b
git -C "$BM_REPO" worktree add "$WT_ROOT/current" HEAD
```

### Step 2: Prepare benchmark datasets once

```bash
cd /path/to/basic-memory/benchmarks
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
just --dry-run bench-qa benchmarks/runs/<run_id>
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
