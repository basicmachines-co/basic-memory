# Semantic Search

This guide covers Basic Memory's optional semantic (vector) search feature, which adds meaning-based retrieval alongside the existing full-text search.

## Overview

Basic Memory's default search uses full-text search (FTS) — keyword matching with boolean operators. Semantic search adds vector embeddings that capture the *meaning* of your content, enabling:

- **Paraphrase matching**: Find "authentication flow" when searching for "login process"
- **Conceptual queries**: Search for "ways to improve performance" and find notes about caching, indexing, and optimization
- **Hybrid retrieval**: Combine the precision of keyword search with the recall of semantic similarity

Semantic search is **opt-in** — existing behavior is completely unchanged unless you enable it. It works on both SQLite (local) and Postgres (cloud) backends.

## Installation

Semantic search dependencies (fastembed, sqlite-vec, openai) are **optional extras** — they are not installed with the base `basic-memory` package. Install them with:

```bash
pip install 'basic-memory[semantic]'
```

This keeps the base install lightweight and avoids platform-specific issues with ONNX Runtime wheels.

### Platform Compatibility

| Platform | FastEmbed (local) | OpenAI (API) |
|---|---|---|
| macOS ARM64 (Apple Silicon) | Yes | Yes |
| macOS x86_64 (Intel Mac) | No — see workaround below | Yes |
| Linux x86_64 | Yes | Yes |
| Linux ARM64 | Yes | Yes |
| Windows x86_64 | Yes | Yes |

#### Intel Mac Workaround

The default FastEmbed provider uses ONNX Runtime, which dropped Intel Mac (x86_64) wheels starting in v1.24. Intel Mac users have two options:

**Option 1: Use OpenAI embeddings (recommended)**

Install only the OpenAI dependency manually — no ONNX Runtime or FastEmbed needed:

```bash
pip install openai sqlite-vec
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=sk-...
```

**Option 2: Pin an older ONNX Runtime**

FastEmbed's ONNX Runtime dependency is unpinned, so you can constrain it to an older version that still ships Intel Mac wheels by passing both requirements in the same install command:

```bash
pip install 'basic-memory[semantic]' 'onnxruntime<1.24'
```

## Quick Start

1. Install semantic extras:

```bash
pip install 'basic-memory[semantic]'
```

2. Enable semantic search:

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

# Traditional full-text search (still the default)
search_notes("login process", search_type="text")
```

## Configuration Reference

All settings are fields on `BasicMemoryConfig` and can be set via environment variables (prefixed with `BASIC_MEMORY_`).

| Config Field | Env Var | Default | Description |
|---|---|---|---|
| `semantic_search_enabled` | `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED` | `false` | Enable semantic search. Required before vector/hybrid modes work. |
| `semantic_embedding_provider` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER` | `"fastembed"` | Embedding provider: `"fastembed"` (local) or `"openai"` (API). |
| `semantic_embedding_model` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL` | `"bge-small-en-v1.5"` | Model identifier. Auto-adjusted per provider if left at default. |
| `semantic_embedding_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` | Auto-detected | Vector dimensions. 384 for FastEmbed, 1536 for OpenAI. Override only if using a non-default model. |
| `semantic_embedding_batch_size` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_BATCH_SIZE` | `64` | Number of texts to embed per batch. |
| `semantic_vector_k` | `BASIC_MEMORY_SEMANTIC_VECTOR_K` | `100` | Candidate count for vector nearest-neighbour retrieval. Higher values improve recall at the cost of latency. |

## Embedding Providers

### FastEmbed (default)

FastEmbed runs entirely locally using ONNX models — no API key, no network calls, no cost.

- **Model**: `BAAI/bge-small-en-v1.5`
- **Dimensions**: 384
- **Tradeoff**: Smaller model, fast inference, good quality for most use cases

```bash
# Install semantic extras and enable
pip install 'basic-memory[semantic]'
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

When switching from FastEmbed to OpenAI (or vice versa), you must rebuild embeddings since the vector dimensions differ:

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

Combines FTS and vector results using reciprocal rank fusion (RRF). This is generally the best mode when you want both keyword precision and semantic recall.

```python
search_notes("authentication security", search_type="hybrid")
```

RRF merges the two ranked lists so that items appearing in both get a score boost, while items found by only one method still appear.

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

- **First enable**: After turning on `semantic_search_enabled` for the first time
- **Provider change**: After switching between `fastembed` and `openai`
- **Model change**: After changing `semantic_embedding_model`
- **Dimension change**: After changing `semantic_embedding_dimensions`

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

Hybrid search uses reciprocal rank fusion (RRF) to merge FTS and vector results:

1. Run FTS search to get keyword-ranked results
2. Run vector search to get similarity-ranked results
3. For each result, compute: `score = 1/(k + fts_rank) + 1/(k + vector_rank)` where `k = 60`
4. Sort by fused score

Items found by both methods get a natural score boost. Items found by only one method still appear but rank lower.

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
- **Chunk metadata table**: Created via Alembic migration (`search_vector_chunks` with `BIGSERIAL` primary key)
- **Embedding table**: `search_vector_embeddings` created at runtime (dimension-dependent, same pattern as SQLite)
- **Index**: HNSW index on the embedding column for fast approximate nearest-neighbour queries

The Alembic migration creates the dimension-independent chunks table. The embeddings table and HNSW index are deferred to runtime because they depend on the configured vector dimensions.
