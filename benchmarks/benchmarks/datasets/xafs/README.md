# xAFS Dataset

xAFS ("Agentic File System") probes agentic retrieval over persona file trees:
13 synthetic personas whose corpora scale from 5 to ~10K files, with questions
answered by reading the files (single-hop, multi-hop, and format-spanning
across non-markdown formats). Headline metric: tokens spent per correct
answer, judged semantically.

- Upstream: https://huggingface.co/datasets/supermemory/xAFS
- License: **CC-BY-4.0** — attribution required.
- **Vendor caveat:** xAFS is authored by supermemory, a memory-product vendor.
  This package reports it as a SECONDARY dataset, never the headline, pending
  a question-quality audit (see `bm-bench sample xafs` and
  docs/benchmarks.md 6d).
- The data is never vendored into this repo; fetch it locally (below).

## Fetch

The dataset is ~19K files / ~837MB in total, so fetch only the personas you
need. The download is pinned to revision `21142b2c` for reproducibility.

```bash
# Two-persona subset (the cheap default the just targets use):
XAFS_PERSONAS=dp_001,dp_002 bash benchmarks/datasets/xafs/download.sh
# or via just:
just bench-fetch-xafs

# Everything (837MB):
bash benchmarks/datasets/xafs/download.sh
```

This downloads into `benchmarks/datasets/xafs/upstream/` (gitignored) using
the `hf` CLI (`pip install -U huggingface_hub`).

## Layout

Each persona directory `dp_001`..`dp_013` contains:

- `data/` — the persona's file tree (mostly `.md`, including double-suffix
  format files like `metrics.csv.md`, plus a handful of real non-markdown
  `.eml` mails)
- `question.json` — the persona's questions: `id`, `family`
  (`single_hop` | `multi_hop` | `format_spanning`), `prompt`,
  `gold_file_ids` (verbatim `data/...` relpaths), `gold_answer`

Only `data/` subtrees are ever ingested; `question.json` (and any
scenario/answer-key files) never reach a corpus.

**Count note:** the upstream README advertises 110 questions (35/50/25 per
family) but the shipped `question.json` files at the pinned revision sum to
33/51/26. The loader trusts the JSON.

## Convert and run

```bash
just bench-convert-xafs                     # dp_001,dp_002 -> benchmarks/generated/xafs
BM_LOCAL_PATH=.. just bench-run-xafs-agent  # agent-task run over the manifest
# or directly:
uv run bm-bench convert xafs --personas dp_001,dp_002
uv run bm-bench run agent-tasks \
  --task-manifest benchmarks/generated/xafs/tasks.json \
  --surfaces rich,posix \
  --model openai-compat:<model>@<base_url> \
  --judge claude:claude-sonnet-4-6 \
  --bm-local-path <bm-checkout>
```

`conversion.json` in the output dir records the pinned revision, selected
personas, per-persona aggregate sha256 over `(relpath, sha256)` pairs, the
`question.json` checksums, and per-extension file counts.

## Audit sampling

```bash
just bench-xafs-audit-sample    # 20 questions, seed 42, stratified by family
```

writes `benchmarks/generated/xafs-audit/` with the sampled prompts, gold
answers, and verbatim copies of every gold source file for human review.
Audit verdicts land in `benchmarks/datasets/xafs/corrections.json` (keyed
`"<persona>/<qid>"`) and feed `bm-bench convert xafs --corrections ...`.
