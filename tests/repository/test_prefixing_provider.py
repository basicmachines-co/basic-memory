"""Tests for role-specific literal embedding text prefixes."""

import hashlib
from typing import Any

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.embedding_provider_factory import _provider_cache_key
from basic_memory.repository.prefixing_provider import (
    PrefixingEmbeddingProvider,
    normalize_embedding_prefix,
)


class _RecordingEmbeddingProvider(EmbeddingProvider):
    model_name = "stub-model"
    dimensions = 3

    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(texts)
        return [[0.0, 1.0, 0.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {"provider_batch_size": 7}


@pytest.mark.asyncio
async def test_prefixing_provider_applies_role_specific_prefixes():
    """Documents and queries should receive their own literal text prefixes."""
    inner = _RecordingEmbeddingProvider()
    provider = PrefixingEmbeddingProvider(
        inner,
        document_prefix="title: none | text: ",
        query_prefix="task: search result | query: ",
    )

    await provider.embed_documents(["indexed chunk"])
    await provider.embed_query("retrieval text")

    assert inner.document_calls == [["title: none | text: indexed chunk"]]
    assert inner.query_calls == ["task: search result | query: retrieval text"]


@pytest.mark.asyncio
async def test_prefixing_provider_preserves_unset_and_empty_prefix_behavior():
    """Unset or empty prefixes should not change provider inputs."""
    inner = _RecordingEmbeddingProvider()
    provider = PrefixingEmbeddingProvider(inner, document_prefix="", query_prefix=None)

    await provider.embed_documents(["indexed chunk"])
    await provider.embed_query("retrieval text")

    assert inner.document_calls == [["indexed chunk"]]
    assert inner.query_calls == ["retrieval text"]
    assert normalize_embedding_prefix("") is None


def test_prefixing_provider_identity_key_uses_prefix_digests():
    """Prefix changes should alter identity without exposing literal values."""
    first = PrefixingEmbeddingProvider(
        _RecordingEmbeddingProvider(),
        document_prefix="doc: ",
        query_prefix="query: ",
    )
    second = PrefixingEmbeddingProvider(
        _RecordingEmbeddingProvider(),
        document_prefix="document: ",
        query_prefix="query: ",
    )

    first_key = first.identity_key()
    second_key = second.identity_key()

    assert first_key != second_key
    assert f"document_prefix_sha256={hashlib.sha256(b'doc: ').hexdigest()}" in first_key
    assert f"query_prefix_sha256={hashlib.sha256(b'query: ').hexdigest()}" in first_key
    assert "doc: " not in first_key
    assert "query: " not in first_key


def test_prefixing_provider_identity_key_distinguishes_unset_from_literal_dash():
    """Unset prefixes must not collide with a real dash prefix."""
    unset_document = PrefixingEmbeddingProvider(
        _RecordingEmbeddingProvider(),
        document_prefix=None,
        query_prefix="query: ",
    )
    dash_document = PrefixingEmbeddingProvider(
        _RecordingEmbeddingProvider(),
        document_prefix="-",
        query_prefix="query: ",
    )

    unset_key = unset_document.identity_key()
    dash_key = dash_document.identity_key()

    assert unset_key != dash_key
    assert "document_prefix_sha256=-" in unset_key
    assert f"document_prefix_sha256={hashlib.sha256(b'-').hexdigest()}" in dash_key


def test_provider_cache_key_does_not_expose_prefix_values():
    """Process-local cache diagnostics should contain digests, not configured text."""
    first = BasicMemoryConfig(
        semantic_embedding_document_prefix="private document role: ",
        semantic_embedding_query_prefix="private query role: ",
    )
    second = BasicMemoryConfig(
        semantic_embedding_document_prefix="different document role: ",
        semantic_embedding_query_prefix="private query role: ",
    )

    first_key = _provider_cache_key(first)
    second_key = _provider_cache_key(second)
    cache_key_text = repr(first_key)

    assert first_key != second_key
    assert "private document role: " not in cache_key_text
    assert "private query role: " not in cache_key_text
    assert hashlib.sha256(b"private document role: ").hexdigest() in cache_key_text
    assert hashlib.sha256(b"private query role: ").hexdigest() in cache_key_text


def test_prefixing_provider_reports_runtime_prefix_status():
    """Runtime logs should expose whether prefixes are enabled without raw text."""
    provider = PrefixingEmbeddingProvider(
        _RecordingEmbeddingProvider(),
        document_prefix="doc: ",
        query_prefix=None,
    )

    assert provider.runtime_log_attrs() == {
        "provider_batch_size": 7,
        "document_prefix_set": True,
        "query_prefix_set": False,
        "document_prefix_length": 5,
    }
