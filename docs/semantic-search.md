# Semantic Search

This guide covers Basic Memory's semantic (vector) search feature, which adds meaning-based retrieval alongside the existing full-text search.

## Overview

Basic Memory's search supports both full-text search (FTS) and semantic retrieval. Semantic search adds vector embeddings that capture the *meaning* of your content, enabling:

- **Paraphrase matching**: Find "authentication flow" when searching for "login process"
- **Conceptual queries**: Search for "ways to improve performance" and find notes about caching, indexing, and optimization
- **Hybrid retrieval**: Combine the precision of keyword search with the recall of semantic similarity

Semantic search is enabled by default when semantic dependencies are available at runtime. It works on both SQLite (local) and Postgres (cloud) backends.

## Installation

Semantic search dependencies (fastembed, sqlite-vec, openai) are included in the default `basic-memory` install.

```bash
pip install basic-memory
```

You can always override with `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true|false`.

### Platform Compatibility

| Platform | FastEmbed (local) | OpenAI (API) |
|---|---|---|
| macOS ARM64 (Apple Silicon) | Yes | Yes |
| macOS x86_64 (Intel Mac) | No — see workaround below | Yes |
| Linux x86_64 | Yes | Yes |
| Linux ARM64 | Yes | Yes |
| Windows x86_64 | Yes | Yes |

#### Intel Mac Workaround

The default install includes FastEmbed, which depends on ONNX Runtime. ONNX Runtime dropped Intel Mac (x86_64) wheels starting in v1.24, so install with a compatible ONNX Runtime pin first:

```bash
pip install basic-memory 'onnxruntime<1.24'
```

After installation, Intel Mac users have two runtime options:

**Option 1: Use OpenAI embeddings (recommended)**

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=sk-...
```

**Option 2: Use FastEmbed locally**

Keep the same pinned installation and use FastEmbed (default provider):

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=fastembed
```

## Quick Start

1. Install Basic Memory:

```bash
pip install basic-memory
```

2. (Optional) Explicitly enable semantic search:

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
```

3. Build vector embeddings for your existing content:

```bash
bm reindex --embeddings
```

4. Search using semantic modes:

```python
# Pure vector similarity
search_notes("login process", search_type="vector")

# Hybrid: combines FTS precision with vector recall (recommended)
search_notes("login process", search_type="hybrid")

# Explicit full-text search
search_notes("login process", search_type="text")
```

## Configuration Reference

All settings are fields on `BasicMemoryConfig` and can be set via environment variables (prefixed with `BASIC_MEMORY_`).

| Config Field | Env Var | Default | Description |
|---|---|---|---|
| `semantic_search_enabled` | `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED` | Auto (`true` when semantic deps are available) | Enable semantic search. Required before vector/hybrid modes work. |
| `semantic_embedding_provider` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER` | `"fastembed"` | Embedding provider: `"fastembed"` (local), `"openai"` (API), or `"litellm"` (multi-provider API, **experimental** — advanced users only). |
| `semantic_embedding_model` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL` | `"bge-small-en-v1.5"` | Model identifier. Auto-adjusted per provider if left at default. |
| `semantic_embedding_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` | Provider default | Vector dimensions. 384 for FastEmbed, 1536 for OpenAI/LiteLLM OpenAI. Required when using a non-default LiteLLM model. |
| `semantic_embedding_forward_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS` | Auto | LiteLLM-only override for whether configured dimensions are sent as a provider-side output-size request. |
| `semantic_embedding_batch_size` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_BATCH_SIZE` | `2` | Number of texts to embed per batch. |
| `semantic_embedding_document_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE` | Auto for known LiteLLM models | Optional LiteLLM `input_type` for indexed document/passages. |
| `semantic_embedding_query_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE` | Auto for known LiteLLM models | Optional LiteLLM `input_type` for search queries. |
| `semantic_vector_k` | `BASIC_MEMORY_SEMANTIC_VECTOR_K` | `100` | Candidate count for vector nearest-neighbour retrieval. Higher values improve recall at the cost of latency. |
| `search_entity_boost_enabled` | `BASIC_MEMORY_SEARCH_ENTITY_BOOST_ENABLED` | `false` | Enable the entity-aware ranking boost in hybrid search (see below). Default off: benchmark-validated as inert on LoCoMo and prone to Title-Case false positives. |
| `search_entity_boost_weight` | `BASIC_MEMORY_SEARCH_ENTITY_BOOST_WEIGHT` | `0.15` | Per-matched-term multiplier strength for the entity boost. A candidate matching N query entity terms is scaled by `1 + weight * min(N, max_terms)`. |
| `search_entity_boost_max_terms` | `BASIC_MEMORY_SEARCH_ENTITY_BOOST_MAX_TERMS` | `3` | Maximum number of distinct matched entity terms that contribute to the boost, bounding the multiplier. |

## Entity-Aware Ranking Boost

Hybrid search fuses keyword (FTS) and vector similarity, but proper nouns in a query
carry no special weight against generic semantic similarity. As a result, a document
about a *different* entity on the same topic can outrank the document that actually
names the queried entity — e.g. "What are Joanna's hobbies?" surfacing a generic
hobbies note ahead of Joanna's note (see
[#951](https://github.com/basicmachines-co/basic-memory/issues/951)).

When `search_entity_boost_enabled=true`, hybrid retrieval performs a final,
lexical-only re-scoring pass:

1. It extracts candidate entity terms from the query — capitalized / proper-noun
   tokens that are not common stopwords (e.g. `Joanna`, `Anthony`, `NASA`).
2. For each fused candidate, it counts how many distinct query entity terms appear in
   the candidate's entity name (its title) or in a relation row's linked entity names.
3. Matching candidates have their fused score multiplied by
   `1 + weight * min(matches, max_terms)`, so an entity-matching document can be
   promoted above a higher-similarity non-matching one.

The boost adds **no model inference** — it is pure index/lexical lookup, so per-query
latency overhead is trivial. It only affects `hybrid` retrieval; `text` and `vector`
modes are unchanged. Non-matching candidates keep their original scores, so ordering
among them is preserved.

```bash
export BASIC_MEMORY_SEARCH_ENTITY_BOOST_ENABLED=true
# Optional tuning:
export BASIC_MEMORY_SEARCH_ENTITY_BOOST_WEIGHT=0.15
export BASIC_MEMORY_SEARCH_ENTITY_BOOST_MAX_TERMS=3
```

> **Default off.** This setting is disabled by default. See the benchmark
> findings below for why the default stays off and where the boost helps.

### Benchmark findings

The boost was benchmarked against LoCoMo (the
[basic-memory-benchmarks](https://github.com/basicmachines-co/basic-memory-benchmarks)
retrieval suite, hybrid mode) and a hand-built adversarial corpus. Two results
drove the decision to keep the default **off** and leave the weight at `0.15`:

1. **LoCoMo is insensitive to the boost.** Sweeping the weight across
   `0.15, 0.3, 0.5, 1.0, 2.0` produced *identical* recall@5, recall@10, MRR, and
   content-hit at every point — no query reordered, no score changed. LoCoMo's
   documents are titled by conversation/session id and expose speaker names only
   in body text, never as entity titles or relation names. Because the boost
   matches query proper nouns against a candidate's **title or linked relation
   names**, it never fires on this corpus. LoCoMo therefore provides no signal to
   raise the weight, and the boost neither helps nor harms it.

2. **A capitalization-only heuristic has false positives.** On a corpus where
   entity terms appear in titles, the boost correctly promotes the right document
   for clean proper nouns (e.g. `Katze`) and is correctly inert on
   lowercase-leading identifiers (e.g. `getUserById`, ignored). But **Title-Case
   queries can regress**: a query like `What Is The Plan For Q3` extracts `Q3` as
   an entity term, and even at weight `0.15` it promotes a document that
   *literally* contains "Q3" above the more relevant document that says "third
   quarter". Since entity detection is lexical (capitalization, no NER), any
   capitalized non-entity token in a query is a potential false positive.

**Guidance.** Enable the boost only on entity-heavy corpora where your queries
name entities that are themselves note titles or linked relations (the #951
"Joanna" case). Prefer natural-case queries (`What are Joanna's hobbies?`) over
Title-Cased phrasing, which can inject spurious entity terms. Leave it off for
conversational / body-text-keyed corpora like LoCoMo, where it cannot help.

## Embedding Providers

### FastEmbed (default)

FastEmbed runs entirely locally using ONNX models — no API key, no network calls, no cost.

- **Model**: `BAAI/bge-small-en-v1.5`
- **Dimensions**: 384
- **Tradeoff**: Smaller model, fast inference, good quality for most use cases

```bash
# Install basic-memory and enable semantic search
pip install basic-memory
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
```

### OpenAI

Uses OpenAI's embeddings API for higher-dimensional vectors. Requires an API key.

- **Model**: `text-embedding-3-small`
- **Dimensions**: 1536
- **Tradeoff**: Higher quality embeddings, requires API calls and an OpenAI key

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=sk-...
```

### LiteLLM

> **Experimental — advanced users only.** The LiteLLM provider is experimental and aimed at users comfortable operating remote embedding backends: paid API calls, per-model dimension and input-role configuration, and slower reindexing of large corpora. For most users, FastEmbed (local, default) is recommended. See [LiteLLM Provider](litellm-provider.md) for the caveats and tuning.

Uses the LiteLLM SDK to call embedding models from providers such as OpenAI, Cohere, Azure, Bedrock, NVIDIA NIM, and other LiteLLM-supported backends. Requires the provider's API credentials.
For the full option reference, provider setup examples, and live validation harness, see [LiteLLM Provider](litellm-provider.md).

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=cohere/embed-english-v3.0
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=1024
export COHERE_API_KEY=...
```

Basic Memory creates vector tables before the first embedding call, so non-default LiteLLM models must set `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS`. The LiteLLM OpenAI default (`openai/text-embedding-3-small`) uses 1536 dimensions automatically.

For fixed-size LiteLLM models, dimensions are used as Basic Memory's local vector schema and
validation size. Basic Memory automatically sends dimensions as a provider-side output-size
request for `text-embedding-3` model strings, where LiteLLM/OpenAI support reduced output
dimensions. If an Azure/OpenAI deployment uses an arbitrary LiteLLM model string such as
`azure/<deployment-name>` and the underlying model supports reduced dimensions, set
`BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS=true`.

Some retrieval models are asymmetric: indexed passages and search queries must be embedded with different provider parameters. Basic Memory automatically sets LiteLLM `input_type` for known asymmetric model families:

- Cohere v3: documents use `search_document`, queries use `search_query`
- NVIDIA NIM retrieval models: documents use `passage`, queries use `query`

For other asymmetric LiteLLM models, set the input types explicitly:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE=passage
export BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE=query
```

#### Live LiteLLM Validation

Provider APIs differ in subtle ways: some accept `dimensions`, some require separate
document/query roles, and some route through deployment aliases that do not reveal the
underlying model name. Before adding or changing LiteLLM model support, run the opt-in live
evaluation harness:

```bash
export OPENAI_API_KEY=sk-...
export COHERE_API_KEY=...
just test-litellm-live
```

The built-in live cases cover:

| Case | Required key | What it validates |
|---|---|---|
| `openai/text-embedding-3-small` | `OPENAI_API_KEY` | Standard LiteLLM OpenAI embedding calls and normalized 1536-dimensional output. |
| `cohere/embed-english-v3.0` | `COHERE_API_KEY` | Cohere v3 asymmetric `search_document` / `search_query` handling and fixed 1024-dimensional output. |

The harness embeds two documents and one query, checks vector dimensions and normalization,
then verifies the authentication query ranks the authentication document above the distractor.
It prints a table with per-model scores, norms, latency, role settings, and dimension-forwarding
mode.

To validate provider aliases or additional LiteLLM backends, save custom JSON cases:

```bash
export AZURE_API_KEY=...
export AZURE_API_BASE=https://example.openai.azure.com
export AZURE_API_VERSION=2024-02-01

cat > /tmp/litellm-azure-cases.json <<'JSON'
[
  {
    "name": "azure-text-embedding-3-small-512",
    "model": "azure/<deployment-name>",
    "dimensions": 512,
    "api_key_env": "AZURE_API_KEY",
    "forward_dimensions": true
  }
]
JSON

just test-litellm-live --cases-file /tmp/litellm-azure-cases.json
```

NVIDIA NIM retrieval models can be checked the same way:

```bash
export NVIDIA_NIM_API_KEY=...

cat > /tmp/litellm-nvidia-cases.json <<'JSON'
[
  {
    "name": "nvidia-embed-qa-4",
    "model": "nvidia_nim/nvidia/embed-qa-4",
    "dimensions": 1024,
    "api_key_env": "NVIDIA_NIM_API_KEY",
    "document_input_type": "passage",
    "query_input_type": "query"
  }
]
JSON

just test-litellm-live --cases-file /tmp/litellm-nvidia-cases.json
```

For repeatable local runs, put the same JSON array in a file and pass
`just test-litellm-live --cases-file path/to/litellm-cases.json`.

When switching providers, models, dimensions, or LiteLLM document/query input types, rebuild embeddings:

```bash
bm reindex --embeddings
```

## Search Modes

### `text` (default)

Full-text keyword search using FTS5 (SQLite) or tsvector (Postgres). Supports boolean operators (`AND`, `OR`, `NOT`), phrase matching, and prefix wildcards.

```python
search_notes("project AND planning", search_type="text")
```

This is the existing default and does not require semantic search to be enabled.

### `vector`

Pure semantic similarity search. Embeds your query and finds the nearest content vectors. Good for conceptual or paraphrase queries where exact keywords may not appear in the content.

```python
search_notes("how to speed up the app", search_type="vector")
```

Returns results ranked by cosine similarity. Individual observations and relations surface as first-class results, not collapsed into parent entities.

### `hybrid`

Combines FTS and vector results using score-based fusion. This is generally the best mode when you want both keyword precision and semantic recall.

```python
search_notes("authentication security", search_type="hybrid")
```

Score-based fusion uses the formula `max(vec, fts) + bonus * min(vec, fts)` to preserve the dominant signal while rewarding results found by both methods.

### When to Use Which

| Mode | Best For |
|---|---|
| `text` | Exact keyword matching, boolean queries, tag/category searches |
| `vector` | Conceptual queries, paraphrase matching, exploratory searches |
| `hybrid` | General-purpose search combining precision and recall |

## The Reindex Command

The `bm reindex` command rebuilds search indexes without dropping the database.

```bash
# Rebuild everything (FTS + embeddings if semantic is enabled)
bm reindex

# Only rebuild vector embeddings
bm reindex --embeddings

# Only rebuild the full-text search index
bm reindex --search

# Target a specific project
bm reindex -p my-project
```

### When You Need to Reindex

- **Upgrade note**: Migration now performs a one-time automatic embedding backfill on upgrade.
- **Manual enable case**: If you explicitly had `semantic_search_enabled=false` and then turn it on
- **Provider change**: After switching between `fastembed`, `openai`, and `litellm`
- **Model change**: After changing `semantic_embedding_model`
- **Dimension change**: After changing `semantic_embedding_dimensions`
- **LiteLLM role change**: After changing `semantic_embedding_document_input_type` or `semantic_embedding_query_input_type`

The reindex command shows progress with embedded/skipped/error counts:

```
Project: main
  Building vector embeddings...
  ✓ Embeddings complete: 142 entities embedded, 0 skipped, 0 errors

Reindex complete!
```

## How It Works

### Chunking

Each entity in the search index is split into semantic chunks before embedding:

- **Headers**: Markdown headers (`#`, `##`, etc.) start new chunks
- **Bullets**: Each bullet item (`-`, `*`) becomes its own chunk for granular fact retrieval
- **Prose sections**: Non-bullet text is merged up to ~900 characters per chunk
- **Long sections**: Oversized content is split with ~120 character overlap to preserve context at boundaries

Each search index item type (entity, observation, relation) is chunked independently, so observations and relations are embeddable as discrete facts.

### Deduplication

Each chunk has a `source_hash` (SHA-256 of the chunk text). On re-sync, unchanged chunks skip re-embedding entirely. This makes incremental updates fast — only modified content triggers API calls or model inference.

### Hybrid Fusion

Hybrid search uses score-based fusion to merge FTS and vector results:

1. Run FTS search to get keyword-ranked results; normalize scores to [0, 1]
2. Run vector search to get similarity-ranked results (already [0, 1])
3. For each result, compute: `fused = max(vec_score, fts_score) + 0.3 * min(vec_score, fts_score)`
4. Sort by fused score

The dominant signal (whichever source scored higher) is preserved, and dual-source agreement adds a bonus. Unlike rank-based fusion, this approach retains score magnitude — a strong vector match stays strong even without an FTS hit.

### Observation-Level Results

Vector and hybrid modes return individual observations and relations as first-class search results, not just parent entities. This means a search for "water temperature for brewing" can surface the specific observation about 205°F without returning the entire "Coffee Brewing Methods" entity.

## Database Backends

### SQLite (local)

- **Vector storage**: [sqlite-vec](https://github.com/asg017/sqlite-vec) virtual table
- **Table creation**: At runtime when semantic search is first used — no migration needed
- **Embedding table**: `search_vector_embeddings` using `vec0(embedding float[N])` where N is the configured dimensions
- **Chunk metadata**: `search_vector_chunks` table stores chunk text, keys, and source hashes

The sqlite-vec extension is loaded per-connection. Vector tables are created lazily on first use.

### Postgres (cloud)

- **Vector storage**: [pgvector](https://github.com/pgvector/pgvector) with HNSW indexing
- **Local Docker**: use `docker-compose-postgres.yml` (`pgvector/pgvector:pg17`). Plain `postgres:17` lacks the extension; run `CREATE EXTENSION IF NOT EXISTS vector;` on any external instance before first migration.
- **Chunk metadata table**: Created via Alembic migration (`search_vector_chunks` with `BIGSERIAL` primary key)
- **Embedding table**: `search_vector_embeddings` created at runtime (dimension-dependent, same pattern as SQLite)
- **Index**: HNSW index on the embedding column for fast approximate nearest-neighbour queries

The Alembic migration creates the dimension-independent chunks table. The embeddings table and HNSW index are deferred to runtime because they depend on the configured vector dimensions.
