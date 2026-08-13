"""OrcaRouter-based embedding provider for cloud or API-backed semantic indexing."""

from __future__ import annotations

import os
from typing import Any, override

from basic_memory.repository.openai_provider import OpenAIEmbeddingProvider
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError

ORCAROUTER_DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"
ORCAROUTER_DEFAULT_MODEL = "openai/text-embedding-3-small"


class OrcaRouterEmbeddingProvider(OpenAIEmbeddingProvider):
    """Embedding provider backed by OrcaRouter's OpenAI-compatible embeddings API.

    OrcaRouter is an OpenAI-compatible model routing gateway. This provider points
    the OpenAI-compatible embedding client at ``https://api.orcarouter.ai/v1`` and
    authenticates with ``ORCAROUTER_API_KEY`` (keys start with ``sk-orca-``).
    Model ids use the gateway's ``provider/model`` form, e.g. ``openai/text-embedding-3-small``.
    """

    def __init__(
        self,
        model_name: str = ORCAROUTER_DEFAULT_MODEL,
        *,
        batch_size: int = 64,
        request_concurrency: int = 4,
        dimensions: int = 1536,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            model_name=model_name,
            batch_size=batch_size,
            request_concurrency=request_concurrency,
            dimensions=dimensions,
            api_key=api_key,
            base_url=base_url or ORCAROUTER_DEFAULT_BASE_URL,
            timeout=timeout,
        )

    @override
    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is not None:
                return self._client

            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - covered via monkeypatch tests
                raise SemanticDependenciesMissingError(
                    "OpenAI dependency is missing. "
                    "Install/update basic-memory to include semantic dependencies: "
                    "pip install -U basic-memory"
                ) from exc

            api_key = self._api_key or os.getenv("ORCAROUTER_API_KEY")
            if not api_key:
                raise SemanticDependenciesMissingError(
                    "OrcaRouter embedding provider requires ORCAROUTER_API_KEY."
                )

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
            return self._client
