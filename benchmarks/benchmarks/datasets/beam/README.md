# BEAM Dataset

BEAM ("Beyond a Million Tokens: Benchmarking the Long-Term Memory of Large
Language Models", arXiv 2510.27246, ICLR 2026) probes long-term conversational
memory across ten abilities over synthetic multi-month chat histories.

- Upstream: https://github.com/mohammadtavakoli78/BEAM
- License: the upstream **code is MIT**; the **benchmark data is CC BY-SA 4.0**
  — attribution and share-alike are required for any redistributed derivative.
  The data is therefore never vendored into this repo; fetch it locally.

## Fetch

The data lives in-tree upstream under `chats/{100K,500K,1M,10M}/<N>/`. Fetch
only the tiers you need with a sparse clone (the full tree is thousands of
blobs):

```bash
bash benchmarks/datasets/beam/download.sh
# or via just:
just bench-fetch-beam
```

This clones into `benchmarks/datasets/beam/upstream/` (gitignored) with only
`chats/100K` and `chats/500K` checked out. To add the 1M tier:

```bash
git -C benchmarks/datasets/beam/upstream sparse-checkout add chats/1M
```

## Tiers

| Tier | Layout | Supported |
| --- | --- | --- |
| 100K | `chats/100K/<N>/` per-conversation | yes |
| 500K | `chats/500K/<N>/` per-conversation | yes |
| 1M   | `chats/1M/<N>/` per-conversation | yes (same layout) |
| 10M  | combined plan-N layout | no (v1 out of scope) |

Each conversation directory contains `chat.json` (and sometimes
`chat_trunecated.json` — sic, upstream misspelling — which is preferred when
present, mirroring upstream's answer generation) plus
`probing_questions/probing_questions.json` keyed by the ten abilities.

## Convert and run

```bash
uv run bm-bench convert beam --tier 100K --output-dir benchmarks/generated/beam-100k
uv run bm-bench run retrieval \
  --dataset-id beam-100k \
  --dataset-path benchmarks/generated/beam-100k/conversion.json \
  --corpus-dir benchmarks/generated/beam-100k/groups \
  --queries-path benchmarks/generated/beam-100k/queries.json \
  --providers bm-local,mem0-local --allow-provider-skip
uv run bm-bench run qa --run-dir benchmarks/runs/<run-id> --answerer ... --judge ...
uv run bm-bench run beam-score --run-dir benchmarks/runs/<run-id> --judge ...
```

`conversion.json` is the provenance manifest the converter writes: it records
the tier, per-conversation source filenames and sha256 checksums of every chat
and probing-questions file consumed. Runs pass it as `--dataset-path` so the
run manifest's dataset checksum pins the exact converted inputs.
