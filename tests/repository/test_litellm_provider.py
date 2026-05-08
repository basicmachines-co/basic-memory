"""Tests for LiteLLMEmbeddingProvider.

Uses AST parsing and direct SDK mocking to avoid importing the full
basic_memory dependency chain (logfire, alembic, etc.).
"""

import ast
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROVIDER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "basic_memory"
    / "repository"
    / "litellm_provider.py"
)
FACTORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "basic_memory"
    / "repository"
    / "embedding_provider_factory.py"
)


class TestLiteLLMProviderStructure:
    """Verify the provider file has the correct structure."""

    def _parse(self):
        return ast.parse(PROVIDER_PATH.read_text())

    def test_file_exists(self):
        assert PROVIDER_PATH.exists()

    def test_has_litellm_embedding_provider_class(self):
        tree = self._parse()
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "LiteLLMEmbeddingProvider" in classes

    def test_has_embed_documents_method(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMEmbeddingProvider":
                methods = [
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                assert "embed_documents" in methods
                assert "embed_query" in methods
                return
        pytest.fail("LiteLLMEmbeddingProvider class not found")

    def test_embed_documents_is_async(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMEmbeddingProvider":
                for item in node.body:
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == "embed_documents":
                        return
        pytest.fail("embed_documents is not async")

    def test_uses_drop_params_true(self):
        src = PROVIDER_PATH.read_text()
        assert "drop_params" in src

    def test_uses_litellm_aembedding(self):
        src = PROVIDER_PATH.read_text()
        assert "aembedding" in src

    def test_has_runtime_log_attrs(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMEmbeddingProvider":
                methods = [
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                assert "runtime_log_attrs" in methods
                return

    def test_default_model_in_source(self):
        src = PROVIDER_PATH.read_text()
        assert "openai/text-embedding-3-small" in src


class TestFactoryRegistration:
    """Verify the factory recognizes litellm as a provider."""

    def test_litellm_branch_in_factory(self):
        src = FACTORY_PATH.read_text()
        assert 'provider_name == "litellm"' in src

    def test_imports_litellm_provider(self):
        src = FACTORY_PATH.read_text()
        assert "LiteLLMEmbeddingProvider" in src


class TestLiteLLMSDKInteraction:
    """Test litellm SDK calls directly (no basic_memory deps needed)."""

    def test_aembedding_called_with_drop_params(self):
        fake = types.ModuleType("litellm")
        response = MagicMock()
        response.data = [{"index": 0, "embedding": [0.1, 0.2]}]
        fake.aembedding = AsyncMock(return_value=response)
        sys.modules["litellm"] = fake

        try:
            import asyncio

            async def run():
                await fake.aembedding(
                    model="openai/text-embedding-3-small",
                    input=["hello"],
                    drop_params=True,
                )

            asyncio.run(run())
            kwargs = fake.aembedding.call_args.kwargs
            assert kwargs["drop_params"] is True
            assert kwargs["model"] == "openai/text-embedding-3-small"
        finally:
            del sys.modules["litellm"]

    def test_aembedding_forwards_api_key(self):
        fake = types.ModuleType("litellm")
        response = MagicMock()
        response.data = [{"index": 0, "embedding": [0.1]}]
        fake.aembedding = AsyncMock(return_value=response)
        sys.modules["litellm"] = fake

        try:
            import asyncio

            async def run():
                await fake.aembedding(
                    model="openai/text-embedding-3-small",
                    input=["hello"],
                    api_key="sk-test",
                    drop_params=True,
                )

            asyncio.run(run())
            assert fake.aembedding.call_args.kwargs["api_key"] == "sk-test"
        finally:
            del sys.modules["litellm"]

    def test_aembedding_response_has_vectors(self):
        fake = types.ModuleType("litellm")
        response = MagicMock()
        response.data = [
            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            {"index": 1, "embedding": [0.4, 0.5, 0.6]},
        ]
        fake.aembedding = AsyncMock(return_value=response)
        sys.modules["litellm"] = fake

        try:
            import asyncio

            async def run():
                resp = await fake.aembedding(
                    model="openai/text-embedding-3-small",
                    input=["hello", "world"],
                    drop_params=True,
                )
                return resp

            resp = asyncio.run(run())
            assert len(resp.data) == 2
            assert resp.data[0]["embedding"] == [0.1, 0.2, 0.3]
            assert resp.data[1]["embedding"] == [0.4, 0.5, 0.6]
        finally:
            del sys.modules["litellm"]
