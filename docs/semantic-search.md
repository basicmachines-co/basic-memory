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

Milvus support is a first-party optional extra, so its heavier client dependencies are installed
only when requested:

```bash
pip install "basic-memory[milvus]"
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

3. Index your project files and build vector embeddings:

```bash
bm reindex
```

Use `bm reindex --embeddings` only after the notes have already been indexed. That flag rebuilds
derived vectors from database entities; it does not discover new files on disk.

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
| `semantic_vector_index` | `BASIC_MEMORY_SEMANTIC_VECTOR_INDEX` | `"pgvector"` | Postgres vector storage adapter: `"pgvector"` or first-party `"milvus"` through the `basic-memory[milvus]` extra. SQLite always uses its built-in `sqlite-vec` adapter. |
| `milvus_uri` | `BASIC_MEMORY_MILVUS_URI` | Unset | Milvus, Milvus Lite, or Zilliz Cloud connection URI. Required when `semantic_vector_index="milvus"`. |
| `milvus_token` | `BASIC_MEMORY_MILVUS_TOKEN` | Unset | Optional Milvus or Zilliz Cloud authentication token. |
| `milvus_timeout_seconds` | `BASIC_MEMORY_MILVUS_TIMEOUT_SECONDS` | `30.0` | Finite per-operation timeout for Milvus and Zilliz client calls. Increase it for unusually slow deployments. |
| `milvus_collection_prefix` | `BASIC_MEMORY_MILVUS_COLLECTION_PREFIX` | `"basic_memory"` | Prefix for deterministic project-isolated Milvus collections. |
| `milvus_database` | `BASIC_MEMORY_MILVUS_DATABASE` | `"default"` | Milvus database name. |
| `semantic_embedding_provider` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER` | `"fastembed"` | Embedding provider: `"fastembed"` (local), `"openai"` (API), or `"litellm"` (multi-provider API, **experimental** — advanced users only). |
| `semantic_embedding_model` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL` | `"bge-small-en-v1.5"` | Model identifier. FastEmbed models must exist in the installed FastEmbed catalog. Auto-adjusted per provider if left at default. |
| `semantic_embedding_api_base` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE` | Unset | Optional custom endpoint for the LiteLLM provider, including local or self-hosted OpenAI-compatible servers. |
| `semantic_embedding_api_key` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY` | Unset | Optional API key passed directly to the LiteLLM provider. When unset, LiteLLM continues to read provider credential env vars such as `OPENAI_API_KEY`. |
| `semantic_embedding_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` | Provider default | Vector dimensions. Defaults to 384 for FastEmbed and 1536 for OpenAI/LiteLLM OpenAI. Set this to the model's output size when choosing a non-default FastEmbed or LiteLLM model. |
| `semantic_embedding_forward_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS` | Auto | LiteLLM-only override for whether configured dimensions are sent as a provider-side output-size request. |
| `semantic_embedding_batch_size` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_BATCH_SIZE` | `2` | Number of texts to embed per batch. |
| `semantic_embedding_document_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE` | Auto for known LiteLLM models | Optional LiteLLM `input_type` for indexed document/passages. |
| `semantic_embedding_query_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE` | Auto for known LiteLLM models | Optional LiteLLM `input_type` for search queries. |
| `semantic_embedding_document_prefix` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_PREFIX` | Unset | Optional literal text prefix prepended to indexed document chunks before embedding. |
| `semantic_embedding_query_prefix` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_PREFIX` | Unset | Optional literal text prefix prepended to search queries before embedding. |
| `semantic_vector_k` | `BASIC_MEMORY_SEMANTIC_VECTOR_K` | `100` | Candidate count for vector nearest-neighbour retrieval. Higher values improve recall at the cost of latency. |

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

#### Choose another local model

Basic Memory passes `semantic_embedding_model` to FastEmbed, so you can select any text
embedding model registered by the installed FastEmbed version. Basic Memory does not maintain a
second model allowlist or add arbitrary Hugging Face models to FastEmbed.

List the models available in the same Python environment as Basic Memory. For a `uv tool`
installation on macOS or Linux:

```bash
"$(uv tool dir)/basic-memory/bin/python" -c \
  'from fastembed import TextEmbedding; print(*(m["model"] + "\t" + str(m["dim"]) for m in TextEmbedding.list_supported_models()), sep="\n")'
```

On Windows PowerShell, use the tool environment's `Scripts` directory:

```powershell
& "$(uv tool dir)\basic-memory\Scripts\python.exe" -c 'from fastembed import TextEmbedding; print(*(m["model"] + "\t" + str(m["dim"]) for m in TextEmbedding.list_supported_models()), sep="\n")'
```

For a virtual-environment or system `pip` installation, run the original installation's
`python` executable with the same `-c` command. Each line shows the model identifier followed by
its output dimensions.

Then configure the model and its output dimensions. For example, the FastEmbed version bundled
with Basic Memory supports the larger mixed Chinese-English Jina model:

```bash
basic-memory config set semantic_embedding_provider fastembed
basic-memory config set semantic_embedding_model jinaai/jina-embeddings-v2-base-zh
basic-memory config set semantic_embedding_dimensions 768
bm reindex --embeddings
```

The model downloads on first use. The configured dimensions must match the model's actual output
size because Basic Memory creates fixed-dimension vector storage before indexing. After changing
the model or dimensions, rebuild embeddings as shown above. A model name absent from
`TextEmbedding.list_supported_models()` requires support in FastEmbed itself or a separately
operated provider such as LiteLLM; setting an arbitrary Hugging Face identifier is not enough.

Some FastEmbed models require different literal prefixes for indexed passages and search queries.
Basic Memory does not infer model-specific prompts for custom models, so configure the prefixes
required by the selected model. For example, multilingual E5 models require `passage: ` for
indexed content and `query: ` for queries:

```bash
basic-memory config set semantic_embedding_provider fastembed
basic-memory config set semantic_embedding_model intfloat/multilingual-e5-large
basic-memory config set semantic_embedding_dimensions 1024
basic-memory config set semantic_embedding_document_prefix "passage: "
basic-memory config set semantic_embedding_query_prefix "query: "
bm reindex --embeddings
```

Rebuild embeddings after changing either prefix so stored document vectors and new query vectors
use the same model contract.

### Chinese, Japanese, Korean and other unsegmented scripts

Keyword (full-text) search does not segment CJK text. Both backends tokenize a run of CJK
characters as a single token — SQLite's FTS5 `unicode61` tokenizer and Postgres's default text
search parser alike — so a query such as `生存` only matches a note where `生存` appears as a
standalone run, not inside `适者生存`. Prefix matching does not help with substrings in the
middle of a run.

Until a segmenting tokenizer option ships (tracked in
[#1294](https://github.com/basicmachines-co/basic-memory/issues/1294)), use semantic or hybrid
search with a multilingual embedding model for these languages — the Jina Chinese-English model
or multilingual E5 configured above both work — and treat keyword search as exact-run matching.

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

For a local or self-hosted OpenAI-compatible embedding server, set the custom
endpoint explicitly:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=openai/local-embedding-model
export BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE=http://127.0.0.1:8080/v1
export BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY=local-key
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=768
```

Leave `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY` unset to keep LiteLLM's normal
environment credential lookup, including `OPENAI_API_KEY`.

Some retrieval models are asymmetric: indexed passages and search queries must be embedded with different provider parameters. Basic Memory automatically sets LiteLLM `input_type` for known asymmetric model families:

- Cohere v3: documents use `search_document`, queries use `search_query`
- NVIDIA NIM retrieval models: documents use `passage`, queries use `query`

For other asymmetric LiteLLM models, set the input types explicitly:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE=passage
export BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE=query
```

Some asymmetric models require literal role text in the input string rather
than, or in addition to, an API `input_type` parameter:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_PREFIX="title: none | text: "
export BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_PREFIX="task: search result | query: "
```

The document prefix is prepended to indexed chunks during sync/reindex. The
query prefix is prepended to search text for vector and hybrid retrieval.
Prefixes work with `fastembed`, `openai`, and `litellm` providers.

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

When switching providers, models, dimensions, or LiteLLM document/query input types,
rebuild embeddings:

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

## Cross-Encoder Reranking

Reranking is an optional second stage for vector and hybrid search. Initial
retrieval finds a candidate pool efficiently; a cross-encoder then reads each
query and candidate together and replaces the leading candidates' scores with
more precise relevance scores.

Reranking is disabled by default. It requires semantic search and does not
change `text`, `title`, or `permalink` search behavior.

### Quick Start

Use the default local FastEmbed provider:

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_RERANKER_ENABLED=true
```

The first reranked search downloads the model when it is not already cached,
then loads `jinaai/jina-reranker-v1-tiny-en`. Later searches reuse the
process-wide model instance and cache.

To use a hosted reranker through LiteLLM:

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_RERANKER_ENABLED=true
export BASIC_MEMORY_RERANKER_PROVIDER=litellm
export BASIC_MEMORY_RERANKER_MODEL=cohere/rerank-v3.5
export COHERE_API_KEY=...
```

LiteLLM model names must use explicit `provider/model` routing. Standard
provider environment variables, such as `COHERE_API_KEY`, work normally. You
can instead set `BASIC_MEMORY_RERANKER_API_KEY` to pass a credential directly
to LiteLLM.

### Providers

| Provider | Runs | Default model | Tradeoff |
|---|---|---|---|
| `fastembed` | Locally with ONNX | `jinaai/jina-reranker-v1-tiny-en` | No API key or per-query cost; downloads a model on first use and adds local inference latency. |
| `litellm` | Hosted or self-hosted API | No implicit hosted default | Supports LiteLLM rerank providers such as Cohere, Jina, and Voyage; adds network latency, provider cost, and credential requirements. |

### Configuration Reference

All settings use the `BASIC_MEMORY_` environment prefix:

| Config Field | Environment Variable | Default | Description |
|---|---|---|---|
| `reranker_enabled` | `BASIC_MEMORY_RERANKER_ENABLED` | `false` | Enable reranking for vector and hybrid search. Requires semantic search. |
| `reranker_provider` | `BASIC_MEMORY_RERANKER_PROVIDER` | `fastembed` | `fastembed` for a local ONNX cross-encoder or `litellm` for an API provider. |
| `reranker_model` | `BASIC_MEMORY_RERANKER_MODEL` | `jinaai/jina-reranker-v1-tiny-en` | Model identifier. LiteLLM requires explicit `provider/model` routing. |
| `reranker_candidates` | `BASIC_MEMORY_RERANKER_CANDIDATES` | `20` | Number of leading retrieval results rescored on every page. Larger values can improve recall but increase latency and provider usage. |
| `reranker_max_document_chars` | `BASIC_MEMORY_RERANKER_MAX_DOCUMENT_CHARS` | `2000` | Maximum characters sent per candidate. The default bounds worst-case latency on very long documents with no measured quality loss; `0` sends the full matched text. |
| `reranker_timeout` | `BASIC_MEMORY_RERANKER_TIMEOUT` | `30.0` | Maximum seconds for each LiteLLM rerank request. FastEmbed runs locally and ignores this setting. |
| `reranker_api_base` | `BASIC_MEMORY_RERANKER_API_BASE` | Unset | Optional custom endpoint for the LiteLLM provider. |
| `reranker_api_key` | `BASIC_MEMORY_RERANKER_API_KEY` | Unset | Optional credential passed directly to LiteLLM. When unset, LiteLLM resolves provider credentials from its normal environment variables. |

Configuration is validated at startup. Basic Memory rejects unsupported
providers, blank models, unavailable FastEmbed models or dependencies, a
LiteLLM model without a provider prefix, and reranking without semantic search.

### Ranking and Pagination Behavior

- Basic Memory reranks the same fixed leading candidate window on every
  non-empty page. Results outside that window retain retrieval order and are
  calibrated at or below the reranked floor. When that floor is zero, stable
  prefix-before-tail ordering breaks the tie.
- The matched chunk is placed before optional title context in the reranker
  document. A positive document-character cap therefore preserves the passage
  that caused the retrieval match.
- Reranker scores replace retrieval scores for the reranked prefix and remain
  within the public `[0, 1]` search score range.
- Multi-project MCP search reranks inside each project's search service, then
  merges the returned project-owned scores. It does not perform a second global
  rerank in the MCP process.

These rules keep independently fetched pages stable while still allowing
larger requests to expand the untouched retrieval tail.

### Failure Behavior

An enabled reranker is part of the requested ranking contract, so Basic Memory
does not silently return retrieval order when it fails:

- Temporary provider, rate-limit, connection, timeout, or first-download
  failures return HTTP 503. Retry the search after the provider recovers.
- Malformed or incomplete provider responses return HTTP 502.
- Authentication, model, dependency, and permanent configuration failures
  surface directly instead of being treated as transient.
- In multi-project search, a retryable failure from any project aborts the
  aggregate page instead of returning a partial result set whose ordering could
  change on retry.

### Tuning

Start with the defaults, then tune only if measurements justify it:

- Increase `reranker_candidates` when relevant results enter the retrieval set
  but remain outside the desired cutoff. This increases local inference time or
  hosted provider usage.
- To reduce rerank latency, lower `reranker_candidates` — per-query cost is
  candidate-count-driven. `reranker_max_document_chars` (default `2000`) only
  matters for very long documents: caps of 2000+ measured identical quality to
  unbounded on a full LoCoMo sweep, while `1000` cost about 1.4 points of
  recall@5. The matched chunk comes first, so the retained prefix carries the
  strongest retrieval signal. Set `0` to disable the cap entirely.
- Keep reranking disabled when retrieval latency matters more than the
  additional ranking pass.

Changing reranker providers, models, candidate counts, or document caps does
not change stored embeddings, so it does not require `bm reindex --embeddings`.

## The Reindex Command

The `bm reindex` command rebuilds search indexes without dropping the database.

Plain `bm reindex` runs the project index before embeddings, so use it for a new project or for
files written directly to disk. `bm reindex --embeddings` intentionally skips file discovery and
only rebuilds vectors for entities already present in the database.

```bash
# Rebuild everything (FTS + embeddings if semantic is enabled)
bm reindex

# Only rebuild vector embeddings (notes must already be indexed)
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
- **Literal prefix change**: After changing `semantic_embedding_document_prefix` or `semantic_embedding_query_prefix`
- **Vector index change**: After completing the vector-store cleanup procedure below and changing `semantic_vector_index`

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
- **Embedding table**: `search_vector_embeddings` using `vec0(embedding float[N], +source_hash text)` where N is the configured dimensions
- **Chunk metadata**: `search_vector_chunks` table stores chunk text, keys, and source hashes

The sqlite-vec extension is loaded per-connection. Vector tables are created lazily on first use.

### Postgres (cloud)

- **Default vector storage**: [pgvector](https://github.com/pgvector/pgvector) with HNSW indexing
- **Local Docker**: use `docker-compose-postgres.yml` (`pgvector/pgvector:pg17`). Plain `postgres:17` lacks the extension; run `CREATE EXTENSION IF NOT EXISTS vector;` on any external instance before first migration.
- **Chunk metadata table**: Created via Alembic migration (`search_vector_chunks` with `BIGSERIAL` primary key)
- **Embedding table**: `search_vector_embeddings` created at runtime (dimension-dependent, same pattern as SQLite)
- **Index**: HNSW index on the embedding column for fast approximate nearest-neighbour queries

The Alembic migration creates the dimension-independent chunks table. The embeddings table and HNSW index are deferred to runtime because they depend on the configured vector dimensions.

## Milvus Vector Index

Postgres deployments can replace pgvector storage and nearest-neighbour lookup without
replacing Basic Memory's SQL repositories or embedding providers. Milvus is the first-party
optional provider:

```bash
pip install "basic-memory[milvus]"
```

```bash
export BASIC_MEMORY_SEMANTIC_VECTOR_INDEX=milvus
export BASIC_MEMORY_MILVUS_URI=http://localhost:19530
export BASIC_MEMORY_MILVUS_TOKEN=root:Milvus  # optional
export BASIC_MEMORY_MILVUS_TIMEOUT_SECONDS=30
```

Zilliz Cloud uses the same URI and token settings. On macOS or Linux, the optional extra also
includes Milvus Lite; set `BASIC_MEMORY_MILVUS_URI` to a local `.db` path. Milvus Lite does not
support Windows, but Windows installations can connect to Milvus Standalone, Milvus Distributed,
or Zilliz Cloud.

Basic Memory applies the configured finite timeout to every remote Milvus operation. Cancellation
waits for the active client call to terminate before releasing the project's SQL mutation lock,
preventing stale writes without letting a half-open connection block the project indefinitely.
Collections and repository reads use strong consistency so a search through a fresh client
observes writes already marked ready in Basic Memory's SQL manifest.

Each project receives one deterministic collection derived from the stable database namespace
and project ID. Collection identity deliberately excludes the embedding model and dimensions. If
an existing collection uses another dimension or an incompatible schema, Basic Memory preserves
it and fails initialization instead of deleting shared vectors during a rolling deployment.
Coordinate the exact collection migration, then run `bm reindex --embeddings`.

SQLite remains automatic in this version: local SQLite databases always select `sqlite-vec`, even
if `semantic_vector_index` is set. The selector controls Postgres-backed runtimes only.

Milvus implements Basic Memory's internal `SemanticVectorIndex` storage boundary. Basic Memory
continues to own chunking, embeddings, and the SQL manifest; Milvus owns only vector persistence,
nearest-neighbour lookup, and scoped orphan cleanup.

### SQL Manifest and Failure Recovery

`search_vector_chunks` remains the authoritative manifest even when vectors live in an external
store. Each row records the selected `vector_index`, embedding identity, stable chunk key, and an
`embedding_status` of `pending` or `ready`.

Writes and deletes commit `pending` before calling the vector index. A successful operation then
makes the manifest row ready or removes it. Vector writes are generation checked inside built-in
SQL adapter transactions; Milvus writes retain the manifest lock across client I/O. If the external
operation fails, the pending row is not searchable and the next sync safely retries the idempotent
operation. Adapter search results are hydrated only through current, ready manifest rows, so stale
or orphaned external matches fail closed.

Basic Memory deliberately refuses to mutate manifest rows owned by a different vector index.
Before switching `semantic_vector_index`, keep the old index configured and remove its
project-scoped vectors. Remove the corresponding
`search_vector_chunks` manifest rows only after the external cleanup succeeds. Then switch the
configured adapter and run `bm reindex --embeddings` to populate the new store. If configuration
was switched too early, restore the old index first; the ownership check will continue to fail
closed until cleanup is completed.
