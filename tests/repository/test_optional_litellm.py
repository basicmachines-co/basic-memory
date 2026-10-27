"""Default providers remain usable when the optional LiteLLM SDK is absent."""

import sys
from typing import Literal

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.openai_provider import OpenAIEmbeddingProvider
from basic_memory.repository.rerank_provider_factory import (
    create_rerank_provider,
    reset_rerank_provider_cache,
)
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError


pytestmark = pytest.mark.real_embedder


@pytest.mark.parametrize("provider", ["fastembed", "openai"])
def test_builtin_provider_does_not_need_litellm(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setitem(sys.modules, "litellm", None)
    reset_embedding_provider_cache()
    config = BasicMemoryConfig(env="test", semantic_embedding_provider=provider)

    embedder = create_embedding_provider(config)

    expected_type = (
        FastEmbedEmbeddingProvider if provider == "fastembed" else OpenAIEmbeddingProvider
    )
    assert isinstance(embedder, expected_type)
    assert create_rerank_provider(config) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["embedding", "rerank"])
async def test_missing_sdk_has_actionable_extra_instructions(
    monkeypatch: pytest.MonkeyPatch, operation: Literal["embedding", "rerank"]
) -> None:
    # Python treats a None module entry as an unavailable package, even when
    # an optional-extra development environment has the SDK installed.
    monkeypatch.setitem(sys.modules, "litellm", None)
    reset_embedding_provider_cache()
    reset_rerank_provider_cache()
    config = BasicMemoryConfig(
        env="test",
        semantic_embedding_provider="litellm",
        reranker_enabled=True,
        reranker_provider="litellm",
        reranker_model="cohere/rerank-v3.5",
    )

    with pytest.raises(SemanticDependenciesMissingError, match=r"basic-memory\[litellm\]") as error:
        if operation == "embedding":
            await create_embedding_provider(config).embed_query("test")
        else:
            reranker = create_rerank_provider(config)
            assert reranker is not None
            await reranker.rerank("test", ["document"])

    assert 'uv tool install --force "basic-memory[litellm]" --prerelease=allow' in str(error.value)
    assert 'pip install "basic-memory[litellm]"' in str(error.value)
