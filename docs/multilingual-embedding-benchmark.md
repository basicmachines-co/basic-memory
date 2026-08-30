# Multilingual Embedding Benchmark

This benchmark supports the evidence-first model selection tracked by
[#1372](https://github.com/basicmachines-co/basic-memory/issues/1372). It does not change Basic
Memory's default embedding model.

## Corpus and measurements

`multilingual-retrieval-v2` contains 17 notes and 23 judged queries covering English, Chinese,
Japanese, Korean, Arabic, Russian, Spanish, Thai, and mixed-language text. The query set includes
same-language retrieval, English-to-non-English retrieval, mixed-language notes, a long-note
chunk-boundary case, and four negative queries. The chunk-boundary judgments count a result only
when the returned matched chunk contains the relevant later-passage text.

Every run uses the production FastEmbed provider and one production storage pairing:
SQLite/sqlite-vec, PostgreSQL/pgvector, or PostgreSQL/Milvus. Milvus runs use the first-party
adapter against an isolated Milvus Lite database, while PostgreSQL continues to own search rows,
the vector manifest, and FTS. Ranking quality is measured without a similarity cutoff. The same
query is then repeated with the configured production cutoff to measure empty results and negative
false positives. JSONL output includes:

- recall@5, MRR@10, wrong-top-result rate, empty-result rate, and negative false-positive rate;
- ranking and thresholded-query p50/p95 latency;
- cold model load, indexing throughput, current/peak RSS, unique model-cache bytes, and vector
  storage bytes;
- model identity, dimensions, prefixes, license, corpus version, FastEmbed/Python versions, and
  host information.

Each model runs in a separate pytest process. This matters because loading multiple ONNX models in
one process contaminates cold-load and resident-memory measurements.

## Running the benchmark

Python 3.12 is the authoritative environment for these measurements.

```bash
# One model/backend pair. Artifacts go to .benchmarks/.
just benchmark-multilingual bge-small-en sqlite vector 0.55
just benchmark-multilingual multilingual-minilm postgres vector 0.55
just benchmark-multilingual multilingual-minilm milvus vector 0.55

# Run and compare the current baseline and the first 384-dimensional candidate.
just benchmark-multilingual-compare sqlite vector 0.55
just benchmark-multilingual-compare postgres vector 0.55
just benchmark-multilingual-compare milvus vector 0.55
```

The recipe installs the locked `milvus` optional extra as needed. The `milvus` backend label means
PostgreSQL metadata/FTS plus Milvus vector storage; it is not a replacement SQL database.

Supported model keys are `bge-small-en`, `multilingual-minilm`, `multilingual-mpnet`, and
`multilingual-e5-large`. E5's required `passage: ` and `query: ` prefixes are part of its benchmark
contract. Jina embeddings v3 is intentionally excluded: the production FastEmbed provider does
not currently express Jina's distinct retrieval-passage and retrieval-query tasks, and its catalog
license requires separate Cloud clearance.

## Initial screening results

These results were collected on 2026-08-30 with Python 3.12.12, FastEmbed 0.8.0, and an Apple
Silicon machine with 8 logical CPUs and 16 GiB RAM. PostgreSQL used the repository's
`pgvector/pgvector:pg16` testcontainer. Milvus used PyMilvus 3.0.0 and Milvus Lite 3.1.0 through
the production adapter. The small corpus proves provider/repository parity and gives directional
model evidence; it is not a Cloud capacity test or a meaningful HNSW scale test.

### SQLite vector quality at the current 0.55 cutoff

| Slice | Metric | BGE small English | Multilingual MiniLM |
| --- | --- | ---: | ---: |
| Overall | recall@5 | 0.8947 | 0.8947 |
| Overall | MRR@10 | 0.7684 | 0.8684 |
| Overall | wrong top | 0.3158 | 0.1579 |
| Overall | accepted empty | 0.0000 | 0.2632 |
| Negative queries | false positive | 0.7500 | 0.0000 |
| Same-language | MRR@10 | 0.8333 | 1.0000 |
| Cross-language | recall@5 | 0.8571 | 1.0000 |
| Cross-language | MRR@10 | 0.7762 | 1.0000 |
| English baseline | MRR@10 | 1.0000 | 1.0000 |
| Chunk boundary | recall@5 | 0.5000 | 0.0000 |

The PostgreSQL/pgvector and PostgreSQL/Milvus runs produced the same vector quality values for both
models. That confirms the model comparison survives both hosted vector-storage paths.

Hybrid retrieval remains backend-sensitive because SQLite FTS5 and PostgreSQL `tsvector` contribute
different normalized lexical scores. Production fusion combines the stronger lexical/vector score
with 30% of the weaker score (`max + 0.3 * min`). The candidate still improved the complete hybrid
path on both backends:

| Backend | Model | recall@5 | MRR@10 | Wrong top | Negative false positive |
| --- | --- | ---: | ---: | ---: | ---: |
| SQLite hybrid | BGE small English | 0.8421 | 0.7268 | 0.3684 | 0.7500 |
| SQLite hybrid | Multilingual MiniLM | 0.8947 | 0.7895 | 0.3158 | 0.0000 |
| PostgreSQL hybrid | BGE small English | 0.8421 | 0.7281 | 0.3684 | 0.7500 |
| PostgreSQL hybrid | Multilingual MiniLM | 0.8947 | 0.7895 | 0.3158 | 0.0000 |
| PostgreSQL/Milvus hybrid | BGE small English | 0.8421 | 0.7281 | 0.3684 | 0.7500 |
| PostgreSQL/Milvus hybrid | Multilingual MiniLM | 0.8947 | 0.7895 | 0.3158 | 0.0000 |

Cross-language hybrid recall@5 increased from 0.7143 to 1.0000 on both backends. Hybrid MRR does
not reach vector-only MRR because the normalized lexical score can still move the correct semantic
result below an FTS result; that is fusion behavior, not a disagreement between sqlite-vec and
pgvector.

In vector-only retrieval, MiniLM improves overall MRR and wrong-top rate, retrieves every
cross-language target within the top five, and rejects every negative query at 0.55. It does not
improve overall recall because neither long-note boundary query retrieves the later legal-hold
passage as the highest-scoring chunk; BGE retrieves one of the two. This is an explicit Cloud test
gate, not a passing result hidden by note-level aggregation.

At the production cutoff, MiniLM has five empty judged positives: the two failed chunk-boundary
oracles plus Japanese watcher reconciliation, English-to-Spanish sourdough retrieval, and the
Japanese mixed-language runbook query below 0.55. At 0.50, the cross-language miss is recovered
without introducing a negative-query false positive; the Japanese same-language and mixed-language
queries remain below the cutoff. A model switch therefore needs an explicit similarity-threshold
decision and long-document validation rather than inheriting 0.55 without measurement.

### Directional local runtime measurements

| Backend | Model | Cold load | Index 17 notes | Notes/sec | Model RSS delta | Cache bytes | Vector storage |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SQLite | BGE small English | 0.25 s | 2.47 s | 6.89 | 221,216,768 | 67,179,926 | 1,638,400 |
| SQLite | Multilingual MiniLM | 0.76 s | 1.49 s | 11.37 | 670,892,032 | 252,141,023 | 1,642,496 |
| PostgreSQL | BGE small English | 0.19 s | 6.06 s | 2.81 | 255,000,576 | 67,179,926 | 270,336 |
| PostgreSQL | Multilingual MiniLM | 1.06 s | 5.28 s | 3.22 | 510,132,224 | 252,141,023 | 262,144 |
| PostgreSQL/Milvus Lite | BGE small English | 0.24 s | 6.46 s | 2.63 | 249,102,336 | 67,179,926 | 159,050 |
| PostgreSQL/Milvus Lite | Multilingual MiniLM | 1.33 s | 4.75 s | 3.58 | 557,875,200 | 252,141,023 | 150,858 |

Local PostgreSQL query latency is dominated by testcontainer and `NullPool` connection setup and
varied substantially between individual queries. Milvus Lite also opens short-lived local clients
through the production repository boundary. The raw artifact preserves those samples, but neither
path should be used to size Cloud workers or set a latency SLO.

SQLite vector storage counts `search_vector_chunks`, its indexes, and the sqlite-vec virtual
table's physical shadow tables through `dbstat`; it excludes entities, FTS rows, and unrelated
database pages. PostgreSQL counts the vector manifest and embedding relations. Milvus Lite counts
the isolated vector database files plus its required PostgreSQL vector manifest.

FastEmbed 0.8.0 reports that multilingual MiniLM now uses mean pooling instead of the CLS pooling
used by older FastEmbed releases. Any rollout decision must therefore pin and record the tested
FastEmbed/model combination; these results should not be treated as portable across provider
version changes without rerunning the corpus.

## Current decision

Multilingual MiniLM advances as the first multilingual Cloud finalist because it materially
improves same-language and cross-language retrieval, preserves the English baseline, remains at
384 dimensions, and has a permissive Apache-2.0 catalog license. It is not an unqualified
replacement: the judged long-note boundary regression must be reproduced and understood on the
Moby-Dick development project, its larger model RSS and 3.75x cache footprint need validation in
the shared Cloud image and tenant-worker process, and the similarity cutoff needs calibration on a
larger judged set.

Do not benchmark the larger candidates by default. Advance MPNet or E5 only if MiniLM fails the
Cloud resource/reindex test or a reviewed corpus exposes a material quality gap. Jina embeddings
v3 requires both task-aware provider support and explicit license clearance before it can become a
valid Cloud benchmark candidate.

## Cloud follow-up

The next bounded task is tracked in
[`basic-memory-cloud#1898`](https://github.com/basicmachines-co/basic-memory-cloud/issues/1898):

1. Bake MiniLM into the shared API/worker image and configure the same model, dimensions, and
   prefixes in both process types.
2. Run a representative tenant reindex on Cloud-equivalent worker hardware while measuring peak
   RSS, duration, failure/retry behavior, image growth, vector-index size, and query latency. Run
   the same corpus against Cloud's configured pgvector or Milvus service before comparing results.
3. Verify that queries targeting later Moby-Dick passages retrieve the relevant passage as the
   highest-scoring chunk, then calibrate the similarity threshold with reviewed multilingual
   judgments.
4. If the evidence still favors MiniLM, deploy the new model identity and use the existing
   revision-deduplicated fleet reindex. Accept temporary semantic incompleteness while FTS remains
   available; do not add blue/green vector storage unless the measured rebuild window requires it.
