"""Voyage AI reranker provider, using the official ``voyageai`` SDK (``basic-memory[voyage]``).

There is no default model: ``reranker_model`` must name one of ``VOYAGE_RERANK_MODELS``.
"""

from __future__ import annotations

from typing import Any

from basic_memory.config_models import VOYAGE_RERANK_MODELS
from basic_memory.repository.rerank_provider import validate_rerank_scores
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
    SemanticDependenciesMissingError,
)
from basic_memory.repository.voyage_provider import (
    import_voyageai,
    resolve_voyage_api_key,
    voyage_error_detail,
)


class VoyageRerankProvider:
    """Reranker provider backed by the official Voyage AI SDK."""

    def __init__(
        self,
        model_name: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if model_name not in VOYAGE_RERANK_MODELS:
            raise ValueError(
                "reranker_provider='voyage' requires reranker_model to name a Voyage rerank "
                f"model; got {model_name!r}. Supported models: {', '.join(VOYAGE_RERANK_MODELS)}."
            )
        self.model_name = model_name
        self._api_key = api_key
        self._timeout = timeout
        self._client: Any | None = None

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []

        voyageai = import_voyageai()
        client = self._client
        if client is None:
            api_key = resolve_voyage_api_key(self._api_key, setting="reranker_api_key")
            # No SDK retries: a rerank failure is reported as a retryable 503 to the caller,
            # and silent backoff would stall every search behind it.
            client = self._client = voyageai.AsyncClient(
                api_key=api_key, max_retries=0, timeout=self._timeout
            )

        errors = voyageai.error
        transient_errors = (
            errors.Timeout,
            errors.APIConnectionError,
            errors.RateLimitError,
            errors.ServerError,
            errors.ServiceUnavailableError,
            errors.TryAgain,
        )
        try:
            # top_k stays unset: the whole candidate pool is rescored, and the caller
            # decides the final cut.
            response = await client.rerank(query, documents, model=self.model_name)
        except transient_errors as exc:
            raise RerankTransientError(
                f"Voyage rerank is temporarily unavailable for model {self.model_name!r}: "
                f"{voyage_error_detail(exc)}"
            ) from exc
        except errors.APIError as exc:
            # An error response the SDK could not classify (for example an HTML page from a
            # proxy) is an upstream fault, not a configuration problem.
            raise RerankProviderContractError(
                f"Voyage rerank returned an unrecognized error for model {self.model_name!r}: "
                f"{voyage_error_detail(exc)}"
            ) from exc
        except errors.VoyageError as exc:
            # Authentication, request, and model errors are configuration faults that retrying
            # cannot fix.
            raise SemanticDependenciesMissingError(
                f"Voyage rerank request failed for model {self.model_name!r}: "
                f"{voyage_error_detail(exc)}"
            ) from exc

        # Results arrive sorted by relevance with their input index. A complete response
        # covers every index exactly once; gaps or repeats are a malformed response that
        # must not be papered over with a 0.0 floor.
        scores: list[float | None] = [None] * len(documents)
        for item in response.results:
            if not 0 <= item.index < len(documents):
                raise RerankProviderContractError(
                    f"Voyage rerank index {item.index} is out of range for "
                    f"{len(documents)} documents."
                )
            if scores[item.index] is not None:
                raise RerankProviderContractError(
                    f"Voyage rerank repeated index {item.index} for {len(documents)} documents."
                )
            scores[item.index] = item.relevance_score
        missing = [index for index, score in enumerate(scores) if score is None]
        if missing:
            raise RerankProviderContractError(
                f"Voyage rerank covered {len(documents) - len(missing)} of "
                f"{len(documents)} documents."
            )
        return validate_rerank_scores(
            [score for score in scores if score is not None], len(documents)
        )
