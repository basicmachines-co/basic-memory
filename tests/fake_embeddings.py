"""Deterministic stand-in for the FastEmbed model in tests.

Semantic search is always on, so every test that writes a note also embeds it. The
real ONNX model costs seconds per sync, so ordinary tests keep the real
FastEmbedEmbeddingProvider class (and therefore the configured embedding identity)
and replace only its two embed methods with a hash of the text. Tests marked
``semantic`` or ``real_embedder`` keep the real model.
"""

import hashlib
import math

import pytest

from basic_memory.repository import semantic_runtime
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider


def fake_unit_vector(text: str, dimensions: int) -> list[float]:
    """Hash text into a unit-length vector, so sqlite-vec cosine math stays valid."""
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
        values.extend((byte - 127.5) / 127.5 for byte in digest)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


async def _embed_query(self: FastEmbedEmbeddingProvider, text: str) -> list[float]:
    return fake_unit_vector(text, self.dimensions)


async def _embed_documents(self: FastEmbedEmbeddingProvider, texts: list[str]) -> list[list[float]]:
    return [fake_unit_vector(text, self.dimensions) for text in texts]


def use_fake_embeddings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap FastEmbed's embed methods unless the test asks for the real model.

    Ordinary tests also model a host that can run vector search, so the sqlite-vec
    capability probe answers yes regardless of whether this Python can load
    extensions. Tests for the keyword-only fallback (#711) inject the opposite.
    """
    if request.node.get_closest_marker("semantic") or request.node.get_closest_marker(
        "real_embedder"
    ):
        return
    monkeypatch.setattr(semantic_runtime, "sqlite_vector_runtime_available", lambda: True)
    monkeypatch.setattr(FastEmbedEmbeddingProvider, "embed_query", _embed_query)
    monkeypatch.setattr(FastEmbedEmbeddingProvider, "embed_documents", _embed_documents)
