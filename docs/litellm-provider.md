# LiteLLM Provider

Basic Memory can use the LiteLLM SDK for semantic search embeddings. This lets you
keep Basic Memory's vector indexing and search behavior while routing embedding calls
to OpenAI-compatible and provider-specific backends such as OpenAI, Azure OpenAI,
Cohere, Bedrock, NVIDIA NIM, and other LiteLLM-supported embedding providers.

Use this page when you want to try a non-default embedding model, validate a provider,
or tune LiteLLM-specific settings.

> **Experimental — advanced users only.** The LiteLLM provider is experimental and
> intended for users who are comfortable operating remote embedding backends. It makes
> paid, networked API calls, requires per-model dimension and input-role configuration,
> and reindexing a real corpus can be slow and spend provider quota (see
> [Reindexing with a remote provider](#reindexing-with-a-remote-provider)). For most
> users, the default local **FastEmbed** provider is the recommended choice. Use LiteLLM
> only if you know what you're doing.

## Quick Start

The default LiteLLM model is OpenAI `text-embedding-3-small` through the LiteLLM
model string `openai/text-embedding-3-small`.

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export OPENAI_API_KEY=sk-...

bm reindex --embeddings
```

Then use vector or hybrid search:

```python
search_notes("login token flow", search_type="hybrid")
```

## Basic Memory Options

All options can be set in config or as environment variables.

| Config Field | Env Var | Default | Notes |
|---|---|---|---|
| `semantic_search_enabled` | `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED` | Auto | Set to `true` to force vector/hybrid support on. |
| `semantic_embedding_provider` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER` | `fastembed` | Set to `litellm` for the LiteLLM provider. |
| `semantic_embedding_model` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL` | `bge-small-en-v1.5` | With `litellm`, the default is remapped to `openai/text-embedding-3-small`. |
| `semantic_embedding_api_base` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE` | Unset | Optional custom endpoint for the LiteLLM provider, including local or self-hosted OpenAI-compatible servers. |
| `semantic_embedding_api_key` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY` | Unset | Optional API key passed directly to the LiteLLM provider. When unset, LiteLLM continues to read provider credential env vars such as `OPENAI_API_KEY`. |
| `semantic_embedding_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` | Provider default | Required for non-default LiteLLM models because vector tables are dimensioned before the first API call. |
| `semantic_embedding_forward_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS` | Auto | Sends `dimensions` to LiteLLM only when supported. Auto is enabled for `text-embedding-3` model strings. |
| `semantic_embedding_document_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE` | Auto | LiteLLM `input_type` for indexed notes/passages. |
| `semantic_embedding_query_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE` | Auto | LiteLLM `input_type` for search queries. |
| `semantic_embedding_document_prefix` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_PREFIX` | Unset | Literal text prefix prepended to indexed document chunks before embedding. |
| `semantic_embedding_query_prefix` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_PREFIX` | Unset | Literal text prefix prepended to search queries before embedding. |
| `semantic_embedding_batch_size` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_BATCH_SIZE` | `2` | Number of text chunks per provider request. |
| `semantic_embedding_request_concurrency` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_REQUEST_CONCURRENCY` | `4` | Maximum concurrent LiteLLM embedding requests. |
| `semantic_embedding_sync_batch_size` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_SYNC_BATCH_SIZE` | `2` | Number of prepared vector jobs flushed through the sync pipeline together. |

## Dimensions

Basic Memory needs the vector dimension before it can create SQLite or Postgres
vector tables. The OpenAI default is known, so this works without an explicit
dimension:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=openai/text-embedding-3-small
```

For every other LiteLLM model, set the dimension explicitly:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=cohere/embed-english-v3.0
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=1024
```

For fixed-size models, `semantic_embedding_dimensions` is Basic Memory's local
schema and validation size. For OpenAI/Azure `text-embedding-3` models, LiteLLM
can also forward `dimensions` as a provider-side reduced-output request. Basic
Memory enables that automatically when the model string contains `text-embedding-3`.

If you use an Azure deployment alias such as `azure/<deployment-name>`, the model
string may not reveal that the underlying model supports reduced output dimensions.
Set this only when your deployment supports it:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS=true
```

## Custom OpenAI-Compatible Endpoints

Set `semantic_embedding_api_base` when an OpenAI-compatible embedding server is
available somewhere other than the provider's default endpoint. Include the API
version prefix expected by the server, commonly `/v1`:

```bash
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=openai/local-embedding-model
export BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE=http://127.0.0.1:8080/v1
export BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY=local-key
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=768
```

`semantic_embedding_api_key` is useful when you want credentials in Basic
Memory's config instead of the process environment. Leave it unset to preserve
LiteLLM's normal provider environment lookup, including `OPENAI_API_KEY`. The
API key can be a placeholder when the local server does not authenticate, but
LiteLLM or the selected backend may still require it to be present.

## Asymmetric Models

Some embedding models use different request roles for indexed documents and
search queries. Basic Memory automatically sets these for known LiteLLM families:

| Model Family | Document `input_type` | Query `input_type` |
|---|---|---|
| Cohere v3 embeddings | `search_document` | `search_query` |
| NVIDIA NIM retrieval embeddings | `passage` | `query` |

For any other asymmetric model, configure both roles explicitly:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE=passage
export BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE=query
```

`input_type` is an API parameter. For models that require role text in the
actual input string, configure literal prefixes instead or in addition:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_PREFIX="title: none | text: "
export BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_PREFIX="task: search result | query: "
```

Changing provider, model, dimensions, dimension-forwarding, document/query roles,
or prefixes changes Basic Memory's stored vector identity. Rebuild embeddings
after any of those changes:

```bash
bm reindex --embeddings
```

## Reindexing with a remote provider

Embedding a real corpus through a network API is far slower than local FastEmbed, and
the defaults are tuned for the local case. Two things to know before you run a full
reindex.

**Raise the sync batch size.** `semantic_embedding_sync_batch_size` defaults to `2`, and
it — not `semantic_embedding_batch_size` — governs throughput on the sync pipeline. With
the default, a full reindex can take tens of seconds *per note* against a remote provider.
Raising both to a larger value turns a multi-minute (or longer) reindex into well under a
minute for the same corpus:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_SYNC_BATCH_SIZE=32
export BASIC_MEMORY_SEMANTIC_EMBEDDING_BATCH_SIZE=64
```

Stay within the provider's per-request size and rate limits — Cohere v3, for example,
accepts up to 96 inputs per embedding request.

**Changing dimensions requires recreating the vector table.** Basic Memory dimensions the
vector table on first index and refuses to mix sizes. Switching to a model with a
different dimension (for example FastEmbed 384 → OpenAI 1536 → Cohere 1024) makes a plain
`bm reindex` raise an `Embedding dimension mismatch` error. Recreate the table with a full
rebuild — files are the source of truth, so this re-indexes from disk and re-embeds
everything:

```bash
bm reset --reindex
```

To trial a provider without disturbing your existing index, point Basic Memory at a
throwaway config + database instead:

```bash
export BASIC_MEMORY_CONFIG_DIR=/tmp/bm-litellm-trial
```

## Provider Setup Examples

LiteLLM reads provider credentials from the environment. These are the examples
covered by Basic Memory's live validation harness.

### OpenAI Through LiteLLM

```bash
export OPENAI_API_KEY=sk-...
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=openai/text-embedding-3-small
```

### Cohere v3

```bash
export COHERE_API_KEY=...
export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=cohere/embed-english-v3.0
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=1024
```

The provider auto-selects `search_document` for indexed chunks and `search_query`
for search queries.

### Azure OpenAI

```bash
export AZURE_API_KEY=...
export AZURE_API_BASE=https://<resource-name>.openai.azure.com
export AZURE_API_VERSION=2024-02-01

export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=azure/<deployment-name>
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=1536
```

If your Azure deployment is a reduced-dimension `text-embedding-3` deployment,
set the dimension you want and enable forwarding:

```bash
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=512
export BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS=true
```

### NVIDIA NIM

```bash
export NVIDIA_NIM_API_KEY=...
# Optional when using a custom or self-hosted NIM endpoint:
export NVIDIA_NIM_API_BASE=https://integrate.api.nvidia.com/v1

export BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true
export BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=litellm
export BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=nvidia_nim/nvidia/embed-qa-4
export BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS=1024
```

The provider auto-selects `passage` for indexed chunks and `query` for search
queries.

## Testing LiteLLM Providers

Run the non-live LiteLLM unit and harness tests first:

```bash
uv run pytest tests/repository/test_litellm_provider.py \
  test-int/semantic/test_litellm_live_harness.py -q
```

Run the SQLite and Postgres vector identity regressions when changing model
identity, role, or vector sync behavior:

```bash
uv run pytest \
  tests/repository/test_sqlite_vector_search_repository.py::test_sqlite_embedding_model_key_includes_litellm_role_settings \
  -q

BASIC_MEMORY_TEST_POSTGRES=1 uv run pytest \
  tests/repository/test_postgres_search_repository.py::test_postgres_litellm_role_change_reembeds_existing_chunks \
  -q
```

The Postgres command uses testcontainers, so Docker must be running.

## Live Provider Harness

The live harness makes real LiteLLM API calls and spends provider quota. It is
opt-in by design:

```bash
export OPENAI_API_KEY=sk-...
export COHERE_API_KEY=...

just test-litellm-live
```

Built-in cases run when their API keys are present:

| Case | Required Env Var | Validates |
|---|---|---|
| `openai-text-embedding-3-small` | `OPENAI_API_KEY` | OpenAI via LiteLLM, 1536 dimensions, normalized vectors, ranking sanity. |
| `cohere-embed-english-v3` | `COHERE_API_KEY` | Cohere v3 role handling, 1024 dimensions, normalized vectors, ranking sanity. |

Add provider aliases or new backends with a custom cases file:

```bash
cat > /tmp/litellm-cases.json <<'JSON'
[
  {
    "name": "azure-text-embedding-3-small-512",
    "model": "azure/<deployment-name>",
    "dimensions": 512,
    "api_key_env": "AZURE_API_KEY",
    "forward_dimensions": true
  },
  {
    "name": "nvidia-embed-qa-4",
    "model": "nvidia_nim/nvidia/embed-qa-4",
    "dimensions": 1024,
    "api_key_env": "NVIDIA_NIM_API_KEY",
    "document_input_type": "passage",
    "query_input_type": "query"
  },
  {
    "name": "local-openai-compatible",
    "model": "openai/local-embedding-model",
    "dimensions": 768,
    "api_key_env": "OPENAI_API_KEY",
    "api_base": "http://127.0.0.1:8080/v1"
  }
]
JSON

just test-litellm-live --cases-file /tmp/litellm-cases.json
```

For CI-style output:

```bash
just test-litellm-live --cases-file /tmp/litellm-cases.json --json
```

The harness embeds two documents and one query, validates dimension and vector
normalization, checks that the authentication query ranks the authentication
document above a distractor, and reports latency plus role/dimension settings.

## Provider Reference

LiteLLM's own provider and embedding docs are the source of truth for current
model strings and credential names:

- [LiteLLM embedding models](https://docs.litellm.ai/docs/embedding/supported_embedding)
- [LiteLLM Azure OpenAI provider](https://docs.litellm.ai/docs/providers/azure)
- [LiteLLM NVIDIA NIM provider](https://docs.litellm.ai/docs/providers/nvidia_nim)
