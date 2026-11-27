"""Voyage AI embedding provider for API-backed semantic indexing.

Uses the official ``voyageai`` SDK, an optional extra (``basic-memory[voyage]``). There is no
default model: every Voyage setup must name its model, because Voyage models are not
interchangeable (their vectors live in different spaces) and picking one silently would
bill the wrong model.

Two endpoint families are supported through one provider:

- ``voyage-4*``, ``voyage-3*``, ``voyage-code-*``, ``voyage-finance-2``, ``voyage-law-2``
  call ``/v1/embeddings``.
- ``voyage-context-4`` and ``voyage-context-3`` call ``/v1/contextualizedembeddings``.
  Basic Memory hands the provider flat chunk texts that are already split and mixed across
  notes, so each chunk is sent as its own one-string document. The vectors come from the
  contextualized models, but there is no cross-chunk context to exploit.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from loguru import logger

from basic_memory.repository.embedding_provider import unit_normalized
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError

# The API accepts at most this many inputs per request.
VOYAGE_MAX_REQUEST_INPUTS = 1000

# Constraint: contextualized auto-chunking splits a document into chunks of at most this
# size. It is the largest chunk size the product is run with, so Basic Memory chunks
# (a few hundred tokens) are never split further, and each document yields one vector.
VOYAGE_CONTEXT_CHUNK_SIZE = 32_000

# Per-request token ceilings. The contextualized models allow 120K total tokens across
# inputs when auto-chunking, which is how indexed chunks are sent.
_TOKENS_1M = 1_000_000
_TOKENS_320K = 320_000
_TOKENS_120K = 120_000

_MATRYOSHKA_DIMENSIONS = (2048, 1024, 512, 256)
_FIXED_DIMENSIONS = (1024,)
_NATIVE_DIMENSIONS = 1024


@dataclass(frozen=True, slots=True)
class VoyageEmbeddingModel:
    """A Voyage embedding model and the output sizes it accepts."""

    name: str
    supported_dimensions: tuple[int, ...]
    max_request_tokens: int
    contextualized: bool = False


VOYAGE_EMBEDDING_MODELS: dict[str, VoyageEmbeddingModel] = {
    model.name: model
    for model in (
        VoyageEmbeddingModel("voyage-4-large", _MATRYOSHKA_DIMENSIONS, _TOKENS_120K),
        VoyageEmbeddingModel("voyage-4", _MATRYOSHKA_DIMENSIONS, _TOKENS_320K),
        VoyageEmbeddingModel("voyage-4-lite", _MATRYOSHKA_DIMENSIONS, _TOKENS_1M),
        VoyageEmbeddingModel("voyage-code-4", _MATRYOSHKA_DIMENSIONS, _TOKENS_120K),
        VoyageEmbeddingModel("voyage-3-large", _MATRYOSHKA_DIMENSIONS, _TOKENS_120K),
        VoyageEmbeddingModel("voyage-3.5", _MATRYOSHKA_DIMENSIONS, _TOKENS_320K),
        VoyageEmbeddingModel("voyage-3.5-lite", _MATRYOSHKA_DIMENSIONS, _TOKENS_1M),
        VoyageEmbeddingModel("voyage-code-3", _MATRYOSHKA_DIMENSIONS, _TOKENS_120K),
        VoyageEmbeddingModel("voyage-finance-2", _FIXED_DIMENSIONS, _TOKENS_120K),
        VoyageEmbeddingModel("voyage-law-2", _FIXED_DIMENSIONS, _TOKENS_120K),
        VoyageEmbeddingModel(
            "voyage-context-4", _MATRYOSHKA_DIMENSIONS, _TOKENS_120K, contextualized=True
        ),
        VoyageEmbeddingModel(
            "voyage-context-3", _MATRYOSHKA_DIMENSIONS, _TOKENS_120K, contextualized=True
        ),
    )
}


def resolve_voyage_embedding_model(
    model_name: str, dimensions: int | None
) -> tuple[VoyageEmbeddingModel, int]:
    """Validate the configured Voyage model and size; return the model and vector size."""
    model = VOYAGE_EMBEDDING_MODELS.get(model_name)
    if model is None:
        supported = ", ".join(VOYAGE_EMBEDDING_MODELS)
        raise ValueError(
            "semantic_embedding_provider='voyage' requires semantic_embedding_model to name "
            f"a Voyage embedding model; got {model_name!r}. Supported models: {supported}."
        )
    if dimensions is None:
        return model, _NATIVE_DIMENSIONS
    if dimensions not in model.supported_dimensions:
        raise ValueError(
            f"semantic_embedding_dimensions={dimensions} is not supported by {model.name!r}. "
            f"Supported dimensions: {', '.join(str(d) for d in model.supported_dimensions)}."
        )
    return model, dimensions


def pack_batches(
    texts: list[str], token_counts: list[int] | None, *, max_inputs: int, max_tokens: int | None
) -> list[list[str]]:
    """Split texts, in order, into batches within the input-count and token ceilings.

    ``token_counts`` and ``max_tokens`` are None when token counts are unavailable, which
    leaves the input-count limit as the only ceiling.
    """
    batches: list[list[str]] = []
    batch: list[str] = []
    batch_tokens = 0
    for index, text in enumerate(texts):
        tokens = token_counts[index] if token_counts is not None else 0
        if max_tokens is not None and tokens > max_tokens:
            raise ValueError(
                f"A chunk of {tokens} tokens exceeds the {max_tokens}-token request limit "
                "of this Voyage model."
            )
        full = len(batch) >= max_inputs or (
            max_tokens is not None and batch_tokens + tokens > max_tokens
        )
        if batch and full:
            batches.append(batch)
            batch, batch_tokens = [], 0
        batch.append(text)
        batch_tokens += tokens
    if batch:
        batches.append(batch)
    return batches


def voyage_embedding_identity(*, model_name: str, dimensions: int) -> str:
    """Return the stored-vector identity for a Voyage embedding setup."""
    return f"{model_name}:{dimensions}"


def voyage_error_detail(error: Any) -> str:
    """Return the message Voyage put in its ``{"detail": "<text>"}`` error body.

    Read the ``detail`` field explicitly so the reported text is exactly what the API
    said, whatever the SDK does with the body; errors that never reached the API
    (timeouts, connection failures) fall back to the SDK message.
    """
    body = error.json_body
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return str(error.user_message or error)


def import_voyageai() -> Any:
    """Import the optional Voyage SDK or explain how to install it."""
    try:
        return import_module("voyageai")
    except ImportError as exc:
        raise SemanticDependenciesMissingError(
            "Voyage embeddings and reranking need the optional SDK. "
            'Install basic-memory[voyage], for example with `pip install "basic-memory[voyage]"` '
            'or `uv tool install --force "basic-memory[voyage]" --prerelease=allow`. '
            "Include any other extras you use in the same specification."
        ) from exc


def resolve_voyage_api_key(api_key: str | None, *, setting: str) -> str:
    """Return the configured key, else VOYAGE_API_KEY, else fail with the setting's name."""
    resolved = api_key or os.getenv("VOYAGE_API_KEY")
    if not resolved:
        raise SemanticDependenciesMissingError(
            f"Voyage provider requires VOYAGE_API_KEY (or {setting})."
        )
    return resolved


class VoyageEmbeddingProvider:
    """Embedding provider backed by the official Voyage AI SDK."""

    def __init__(
        self,
        model_name: str,
        *,
        batch_size: int = 64,
        request_concurrency: int = 4,
        dimensions: int | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not 0 < batch_size <= VOYAGE_MAX_REQUEST_INPUTS:
            raise ValueError(
                f"semantic_embedding_batch_size must be between 1 and {VOYAGE_MAX_REQUEST_INPUTS} "
                f"for the Voyage provider; got {batch_size}."
            )
        self._model, self.dimensions = resolve_voyage_embedding_model(model_name, dimensions)
        self.model_name = model_name
        self.batch_size = batch_size
        self.request_concurrency = request_concurrency
        # Voyage is asked for a specific size only when one was configured.
        self._requested_dimension = dimensions
        self._api_key = api_key
        self._timeout = timeout
        self._client: Any | None = None
        # Set once the tokenizer failed to load, so a flaky network is not retried per flush.
        self._tokenizer_unavailable = False

    def runtime_log_attrs(self) -> dict[str, Any]:
        """Return the request fan-out knobs that shape API embedding batches."""
        return {
            "provider_batch_size": self.batch_size,
            "request_concurrency": self.request_concurrency,
            "contextualized": self._model.contextualized,
        }

    def identity_key(self) -> str:
        """Return the embedding semantics that should invalidate stored vectors."""
        return voyage_embedding_identity(model_name=self.model_name, dimensions=self.dimensions)

    def _get_client(self) -> Any:
        # No lock: building the client awaits nothing, so concurrent batches on one event
        # loop cannot interleave inside this method.
        if self._client is None:
            voyageai = import_voyageai()
            api_key = resolve_voyage_api_key(self._api_key, setting="semantic_embedding_api_key")
            self._client = voyageai.AsyncClient(
                api_key=api_key, max_retries=2, timeout=self._timeout
            )
        return self._client

    async def _request(self, batch: list[str], *, input_type: str) -> list[list[float]]:
        """Embed one batch with a single API call and return vectors in input order."""
        client = self._get_client()
        voyageai_error = import_voyageai().error.VoyageError
        try:
            if self._model.contextualized:
                vectors = await self._request_contextualized(client, batch, input_type)
            else:
                response = await client.embed(
                    batch,
                    model=self.model_name,
                    input_type=input_type,
                    # An over-long input must fail, not be embedded from a silent prefix.
                    truncation=False,
                    output_dimension=self._requested_dimension,
                )
                vectors = response.embeddings
        except voyageai_error as exc:
            raise RuntimeError(
                f"Voyage embedding request failed for model {self.model_name!r}: "
                f"{voyage_error_detail(exc)}"
            ) from exc

        if len(vectors) != len(batch):
            raise RuntimeError(
                f"Voyage returned {len(vectors)} vectors for {len(batch)} inputs "
                f"with model {self.model_name!r}."
            )
        return [unit_normalized([float(value) for value in vector]) for vector in vectors]

    async def _request_contextualized(
        self, client: Any, batch: list[str], input_type: str
    ) -> list[Sequence[float]]:
        # Constraint: auto-chunking only works with input_type="document", and then needs
        # one string per document; queries cannot be auto-chunked and are one-string inputs.
        kwargs: dict[str, Any] = {}
        if input_type == "document":
            kwargs["enable_auto_chunking"] = True
            kwargs["chunk_size"] = VOYAGE_CONTEXT_CHUNK_SIZE
        response = await client.contextualized_embed(
            batch,
            model=self.model_name,
            input_type=input_type,
            output_dimension=self._requested_dimension,
            **kwargs,
        )
        vectors: list[Sequence[float]] = []
        for index, result in enumerate(response.results):
            if len(result.embeddings) != 1:
                raise RuntimeError(
                    f"Voyage split input {index} into {len(result.embeddings)} chunks with "
                    f"model {self.model_name!r}; expected one vector per Basic Memory chunk."
                )
            vectors.append(result.embeddings[0])
        return vectors

    async def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []

        # Token-aware batching: sum the model's own token counts so a batch of long chunks
        # never exceeds the request ceiling the API would reject it for. The tokenizer is
        # loaded once by the SDK and counting runs off the event loop.
        token_counts: list[int] | None = None
        # A single input (every search query) can never be split, and an oversize one is
        # rejected by the API, so only multi-chunk batches pay for tokenization.
        if len(texts) > 1 and not self._tokenizer_unavailable:
            client = self._get_client()
            try:
                encodings = await asyncio.to_thread(client.tokenize, texts, self.model_name)
            except (OSError, ImportError) as exc:
                # Trigger: the SDK fetches the tokenizer from huggingface.co on first use, and
                # that can be blocked or offline (Hugging Face's errors are OSErrors); a missing
                # tokenizers package is an ImportError. Any other error is a bug and propagates.
                # Why: the token cap is a batching optimization; the input-count limit
                # still bounds every request, and an oversize one is rejected by the API.
                # Outcome: batches are packed by input count alone, and the tokenizer is not
                # retried for the life of this provider.
                self._tokenizer_unavailable = True
                logger.warning(
                    "Voyage tokenizer unavailable for model {model}; batching by input count "
                    "only: {error}",
                    model=self.model_name,
                    error=exc,
                )
            else:
                token_counts = [len(encoding.ids) for encoding in encodings]
        batches = pack_batches(
            texts,
            token_counts,
            max_inputs=self.batch_size,
            max_tokens=None if token_counts is None else self._model.max_request_tokens,
        )
        semaphore = asyncio.Semaphore(self.request_concurrency)

        async def embed_batch(batch: list[str]) -> list[list[float]]:
            async with semaphore:
                return await self._request(batch, input_type=input_type)

        # gather returns results in batch order, which is the input order.
        batch_vectors = await asyncio.gather(*(embed_batch(batch) for batch in batches))
        all_vectors = [vector for vectors in batch_vectors for vector in vectors]
        if len(all_vectors[0]) != self.dimensions:
            raise RuntimeError(
                f"Embedding model returned {len(all_vectors[0])}-dimensional vectors "
                f"but provider was configured for {self.dimensions} dimensions."
            )
        return all_vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, input_type="document")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], input_type="query")
        return vectors[0]
