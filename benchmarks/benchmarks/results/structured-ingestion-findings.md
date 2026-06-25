# Structured ingestion: does Basic Memory's native representation help?

**Date:** 2026-06-14 · **Status:** internal finding · **Harness:** `convert structure-corpus`

## Question

The default benchmark ingests each conversation as a flat `## Conversation`
transcript, so Basic Memory is exercised purely as a text-search engine — none
of its native representation (typed `- [category]` observations and
`- relation [[Entity]]` links) is used. Does running BM "at its best", with the
conversations distilled into its knowledge-graph form, change the numbers?

## Method

`convert structure-corpus` rewrites each flat conversation doc into a BM-native
note via the plan-billed `claude -p` extractor (no API spend), in two modes:

- **augment** (faithful): keep the transcript **and** append an extracted
  `## Observations` + `## Relations` block. This mirrors how BM actually
  works — a note keeps its prose while its parsed observations/relations become
  first-class searchable units.
- **replace**: substitute the structured block for the transcript (lossy).

Document ids and frontmatter are preserved, so retrieval ground truth, recall,
QA, and the failure diagnostic stay directly comparable to the flat corpus.
Answerer (`claude-haiku-4-5`) and judge (`claude-sonnet-4-6`) are held constant;
only the corpus representation changes.

## Results

### ConvoMem `implicit_connection` (49 q) — retrieval saturated

| arm | QA acc | correct |
|---|---|---|
| flat (transcripts) | **0.449** | 22/49 |
| structured-replace (lossy) | 0.367 | 18/49 |
| structured-augment (faithful) | **0.449** | 22/49 |

Augment **ties** flat exactly (it flips 10/49 questions, +5/−5 — a wash);
replace **hurts** because distillation drops the specific personal details these
questions hinge on. **Why:** ConvoMem cs10 retrieval is *saturated* (the
diagnostic shows a retrieval ceiling of 1.000 — the answerer already receives the
entire 10-doc haystack), so representation can only reshuffle what is already in
context. **Implication:** flat markdown is a *fair* representation for BM on
ConvoMem — the benchmark was not secretly hobbling BM there.

### LoCoMo `multi_hop` (63 q, full 272-doc haystack) — retrieval has headroom

| metric | flat | structured-augment | Δ |
|---|---|---|---|
| recall@5 | 0.778 | **0.833** | **+0.055** |
| recall@10 | 0.841 | **0.889** | **+0.048** |
| MRR | 0.679 | **0.715** | +0.036 |
| QA accuracy | 0.556 (35/63) | 0.571 (36/63) | +0.015 |

Structure **improves retrieval**: +5 questions newly reach their gold evidence in
the top-10 (−2 lost, net +3). Example — *"Where did Joanna travel in July 2022?"*:
flat never retrieves the evidence (→ "I don't know"); structured does (→
"Woodhaven", correct). QA barely moves (+1) because the diagnostic attributes 81%
of multi_hop failures to the fixed answerer, so retrieval gains don't fully
convert end-to-end.

## Interpretation

Structure is a **wash where retrieval saturates** (ConvoMem) and **helps recall
where retrieval has headroom** (LoCoMo multi_hop) — a coherent two-slice story.

**Mechanism (important for credibility):** Basic Memory does **not** traverse
relations at query time today, so the recall gain is **not** graph multi-hop. It
comes from the concise observations being *better retrieval targets* than the same
facts buried in a long transcript — a better lexical/semantic surface.

**Confound to close before publishing:** augmented docs are longer (transcript +
structure), giving more match surface. A replace-mode retrieval ablation would
isolate "structure" from "more text". The LoCoMo effect is also modest (n=63);
confirm on the full q300 / multi_hop+temporal slice before any external claim.

**Product lever this surfaces:** following `[[Entity]]` relations during search
(query-time relation traversal) would turn the structured representation into
genuine multi-hop retrieval — a concrete future basic-memory retrieval
improvement, distinct from the lexical-surface gain measured here.

## Reproduce

```bash
# Structure a corpus (flat or grouped layout; augment keeps the transcript)
bm-bench convert structure-corpus \
  --input-dir benchmarks/generated/locomo-corrected-v2/docs \
  --output-dir benchmarks/generated/locomo-corrected-v2-augmented/docs \
  --mode augment --extractor claude:claude-haiku-4-5

# Run flat vs structured over the same queries; compare recall + QA + diagnose
bm-bench run retrieval --providers bm-local --corpus-dir <corpus> --queries-path <queries> ...
bm-bench run qa --run-dir <run> --answerer claude:claude-haiku-4-5 --judge claude:claude-sonnet-4-6
bm-bench run diagnose --run-dir <run>
```
