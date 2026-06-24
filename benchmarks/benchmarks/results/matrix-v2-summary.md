# Benchmark Matrix v2 — internal results (publication-ready)

Supersedes `matrix-v1.2-summary.md` (pre-rubric-correction, pre-#994). Same
harness, corrected judge rubric (basic-memory-benchmarks #36/#37), BM under test
is **`main` post-#994** (FTS-revival merged). Internal-only; written to withstand
external scrutiny.

## Methodology (what makes these numbers fair)

- **Zero API spend.** Answering and judging run through the Claude plan
  (`claude -p`); competitor internal LLM calls (mem0 extraction) run on local
  Ollama. No paid API.
- **Fixed answerer and judge, identical for every provider.** Answerer
  `claude:claude-haiku-4-5`, judge `claude:claude-sonnet-4-6`. Each provider
  retrieves; the *same* answerer writes an answer from the retrieved memories;
  the *same* judge grades it against gold. Holding the answerer constant is what
  isolates retrieval — but it also means **absolute QA accuracy is
  answerer-dependent** (a stronger answerer would raise every number). Relative
  standings are the robust comparison.
- **Corrected rubric**, validated on all gold-answer styles including abstention
  (the #36→#37 fix). Judge decisions are auditable per case via `run review`
  HTML reports.
- **`run diagnose`** attributes every QA failure to *retrieval* (gold not found)
  vs the *answerer* (gold found, answer still wrong) — see below.
- No feature flags: BM runs default `main`.

## Anchors

LongMemEval-S (stratified 60, 6 categories) and ConvoMem cs10 (274). LoCoMo is
secondary (Penfield-corrected key, adversarial excluded) and reported separately.

### 1. Retrieval — the answerer-independent signal (lead with this)

| Benchmark | provider | recall@5 | recall@10 | MRR | mean lat |
|---|---|---|---|---|---|
| LongMemEval-60 | bm-local | 0.951 | 0.951 | **0.900** | 754ms |
| LongMemEval-60 | mem0-local | **0.979** | 0.992 | 0.876 | 146ms |
| LongMemEval-60 | baseline-grep | 0.846 | 0.937 | 0.832 | 5ms |
| ConvoMem-274 | bm-local | 0.982 | 0.996 | 0.929 | 140ms |
| ConvoMem-274 | mem0-local | **0.996** | 1.000 | **0.956** | 122ms |
| ConvoMem-274 | baseline-grep | 0.954 | 1.000 | 0.863 | 1ms |

Retrieval is **near-parity**: mem0 marginally leads recall@5, BM leads MRR on
LongMemEval and is within ~0.03 on ConvoMem. Both systems find the gold evidence
almost always (recall@10 ≥ 0.95). grep is a strong lexical baseline here.

### 2. QA accuracy — corrected rubric (BM leads decisively)

| Benchmark | bm-local | mem0-local | baseline-grep | full-context |
|---|---|---|---|---|
| LongMemEval-60 | **0.583** | 0.450 | 0.333 | 0.217 |
| ConvoMem-274 | **0.755** | 0.464 | 0.380 | 0.799 |

Abstention rates (answerer says "I don't know"): LongMemEval BM **20**/60 vs mem0
30/60; ConvoMem BM **86**/274 vs mem0 169/274. mem0 abstains ~2× more often.

### 3. Diagnostic — why BM's QA lead is NOT a retrieval effect

`run diagnose`, per provider, over answerable questions:

| Benchmark | provider | retrieval ceiling | answerer gap | retrieval gap | of failures: answerer |
|---|---|---|---|---|---|
| LongMemEval-60 | bm-local | 0.983 | 0.400 | 0.017 | 96% |
| LongMemEval-60 | mem0-local | 1.000 | 0.550 | 0.000 | 100% |
| ConvoMem-274 | bm-local | 1.000 | 0.245 | 0.000 | 100% |
| ConvoMem-274 | mem0-local | 1.000 | 0.535 | 0.000 | 100% |

The retrieval ceiling (max QA if the answerer were perfect) is ~1.0 for **both**
systems — i.e. essentially every QA failure is "the gold evidence *was* retrieved,
the answer was still wrong," not "retrieval missed it." So the BM>mem0 QA gap is
**not** a recall gap. It is that **the memories BM returns are more answerable by
the fixed model** — BM returns dated, in-context chunks; mem0 (raw-add) returns
material the small answerer more often can't commit to, so it abstains. On
ConvoMem the haystack fully fits the retrieval window (ceiling 1.000 for
everyone), so that benchmark measures *presentation + answering*, not recall.

**Honest reading:** lead published comparisons with **recall@k / MRR** (parity,
answerer-independent). Present QA as a secondary "answer-bearing context" signal
where BM leads — with the mem0-infer caveat below kept prominent.

## LoCoMo (secondary) — BM product progression

LoCoMo is Penfield-flagged (~6.4% gold-key errors; adversarial excluded). We
report BM's own improvement across the two shipped fixes, same q300 subset and QA
stage, corrected rubric:

| QA accuracy | BM main | +FTS (#994) | +FTS +title |
|---|---|---|---|
| overall | 0.439 | 0.475 | **0.641** |

multi_hop is the driver (4/63 → 40/63): these are relative-date questions that
were unanswerable until the dated session title was surfaced to the answerer
(harness fix #31, provider-faithful — BM already returns the title). BM retrieval
on this slice: recall@5 0.774, recall@10 0.875, MRR 0.697. Diagnostic: ceiling
0.930, 19% of failures are true retrieval misses (the only anchor with real
retrieval headroom). A corrected-rubric mem0 head-to-head on this exact subset is
**not yet run** — the earlier mem0 LoCoMo number (0.535) predates the rubric fix
and is not directly comparable.

## supermemory-local — preliminary, fair full run blocked

Provider works end-to-end vs `supermemory-server 0.0.2` (local, Ollama-backed).
Preliminary 35-q ConvoMem smoke: QA tied (both 0.943), BM search **3.4× faster**
(84ms vs 289ms). A fair full run is **blocked by upstream #1096**: supermemory's
memory-agent calls the OpenAI Responses API (Ollama rejects it), spending ~228s/doc
before skipping, so grouped ingest times out. Also #1093: on-device embedding RSS
ballooned to ~24GB. Two findings stand: supermemory-local is operationally heavy
(slow + memory-hungry vs BM's lightweight fastembed), and on the slice that
completed, BM matches its QA and is markedly faster. Needs a
Responses→ChatCompletions shim for a complete comparison.

## Caveats (read before citing)

- **mem0 ran raw-add (`infer=false`).** mem0's published numbers use `infer=true`
  (LLM fact extraction). We matched the June-10 baseline and avoided an unvetted
  local-3B extraction step. mem0's QA could improve under `infer=true`; a fair
  external comparison must run mem0 both ways with the extraction model documented.
- **QA is answerer-bound** (the diagnostic). These absolute numbers reflect a
  haiku-class answerer; a stronger answerer raises all of them. Don't read QA as a
  pure retrieval-quality measure — that's what recall@k/MRR are for.
- **Judge-human agreement not yet measured.** Every QA number rests on the LLM
  judge. The `run review` HTML reports support human labeling; the judge-vs-human
  agreement pass is the remaining validation before any external publication.
- **LoCoMo** gold key is documented-imperfect; treat as directional.
- **n sizes**: LongMemEval 60, LoCoMo q300 subset — modest; ConvoMem 274.

## Remaining for external publication

1. **Judge-human agreement pass.** A balanced 60-case sample is ready at
   `benchmarks/runs/judge-agreement-sample/review.html` (seed 42; 24 LongMemEval
   / 24 ConvoMem / 12 LoCoMo; 36 BM / 12 mem0 / 12 grep; 20 correct / 20 incorrect
   / 20 abstain). Open it, label each verdict agree/disagree/unsure, Export, and
   report agreement = agree / (agree + disagree). Publish the agreement rate
   alongside the numbers — every QA figure rests on the judge.
2. mem0 `infer=true` (and corrected-rubric mem0 on LoCoMo q300) to complete the matrix.
3. supermemory Responses→ChatCompletions shim (#1096) for a fair full run.
