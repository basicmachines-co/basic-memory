# LiteLLM Provider

Basic Memory can use the LiteLLM SDK for semantic search embeddings. This lets you
keep Basic Memory's vector indexing and search behavior while routing embedding calls
to OpenAI-compatible and provider-specific backends such as OpenAI, Azure OpenAI,
Cohere, Bedrock, NVIDIA NIM, and other LiteLLM-supported embedding providers.

Use this page when you want to try a non-default embedding model, validate a provider,
or tune LiteLLM-specific settings.

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
| `semantic_embedding_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS` | Provider default | Required for non-default LiteLLM models because vector tables are dimensioned before the first API call. |
| `semantic_embedding_forward_dimensions` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_FORWARD_DIMENSIONS` | Auto | Sends `dimensions` to LiteLLM only when supported. Auto is enabled for `text-embedding-3` model strings. |
| `semantic_embedding_document_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_DOCUMENT_INPUT_TYPE` | Auto | LiteLLM `input_type` for indexed notes/passages. |
| `semantic_embedding_query_input_type` | `BASIC_MEMORY_SEMANTIC_EMBEDDING_QUERY_INPUT_TYPE` | Auto | LiteLLM `input_type` for search queries. |
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

Changing provider, model, dimensions, dimension-forwarding, or document/query
roles changes the meaning of stored vectors. Rebuild embeddings after any of
those changes:

```bash
bm reindex --embeddings
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
