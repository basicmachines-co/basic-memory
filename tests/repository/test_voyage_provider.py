"""Failure modes of the Voyage embedding provider and its factory wiring."""

import dataclasses
import math
import sys

from collections.abc import Iterator

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
    create_embedding_provider,
    reset_embedding_provider_cache,
)
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.voyage_provider import (
    VoyageEmbeddingProvider,
    import_voyageai,
    pack_batches,
    resolve_voyage_embedding_model,
    voyage_error_detail,
)
from tests.repository.voyage_fakes import (
    FakeAsyncClient,
    ERRORS,
    contextualized_response,
    embedding_response,
    install_fake_voyageai,
)


@pytest.fixture(autouse=True)
def _voyage_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


# --- Explicit model selection ---


@pytest.mark.parametrize(
    "model_name", ["bge-small-en-v1.5", "openai/text-embedding-3-small", "voyage-nope", ""]
)
def test_model_must_be_a_named_voyage_model(model_name: str) -> None:
    with pytest.raises(ValueError, match="requires semantic_embedding_model"):
        resolve_voyage_embedding_model(model_name, None)


def test_default_config_model_is_rejected_for_voyage_provider() -> None:
    config = BasicMemoryConfig(env="test", semantic_embedding_provider="voyage")

    with pytest.raises(ValueError, match="requires semantic_embedding_model"):
        create_embedding_provider(config)
    with pytest.raises(ValueError, match="requires semantic_embedding_model"):
        configured_embedding_provider_identity(config)


@pytest.mark.parametrize(
    ("model_name", "dimensions"),
    [("voyage-4", 300), ("voyage-4", 4096), ("voyage-finance-2", 512), ("voyage-law-2", 2048)],
)
def test_unsupported_dimensions_are_rejected(model_name: str, dimensions: int) -> None:
    with pytest.raises(ValueError, match="not supported"):
        resolve_voyage_embedding_model(model_name, dimensions)


@pytest.mark.parametrize("batch_size", [0, 1001])
def test_batch_size_is_bounded_by_the_request_input_limit(batch_size: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 1000"):
        VoyageEmbeddingProvider("voyage-4", batch_size=batch_size)


def test_custom_api_base_is_rejected() -> None:
    config = BasicMemoryConfig(
        env="test",
        semantic_embedding_provider="voyage",
        semantic_embedding_model="voyage-4",
        semantic_embedding_api_base="http://localhost:1234",
    )

    with pytest.raises(ValueError, match="semantic_embedding_api_base"):
        create_embedding_provider(config)
    with pytest.raises(ValueError, match="semantic_embedding_api_base"):
        configured_embedding_provider_identity(config)


# --- Request shape ---


@pytest.mark.asyncio
async def test_output_dimension_is_sent_only_when_configured(monkeypatch) -> None:
    client = install_fake_voyageai(
        monkeypatch,
        embedding_response([3.0, 4.0] + [0.0] * 1022),
        embedding_response([3.0, 4.0] + [0.0] * 254),
    )

    native = VoyageEmbeddingProvider("voyage-4")
    await native.embed_query("a")
    reduced = VoyageEmbeddingProvider("voyage-4", dimensions=256)
    vector = await reduced.embed_query("a")

    native_call, reduced_call = (call for instance in client.instances for call in instance.calls)
    assert native_call[2]["output_dimension"] is None
    assert reduced_call[2]["output_dimension"] == 256
    assert native_call[2]["input_type"] == reduced_call[2]["input_type"] == "query"
    assert native_call[2]["truncation"] is False
    assert len(vector) == 256


@pytest.mark.asyncio
async def test_documents_are_batched_in_order_with_document_input_type(monkeypatch) -> None:
    client = install_fake_voyageai(
        monkeypatch,
        embedding_response([1.0] + [0.0] * 1023, [0.0, 1.0] + [0.0] * 1022),
        embedding_response([0.0, 0.0, 1.0] + [0.0] * 1021),
    )
    provider = VoyageEmbeddingProvider("voyage-4", batch_size=2, request_concurrency=1)

    vectors = await provider.embed_documents(["a", "b", "c"])

    calls = client.instances[0].calls
    assert [call[1][0] for call in calls] == [["a", "b"], ["c"]]
    assert {call[2]["input_type"] for call in calls} == {"document"}
    assert [vector.index(1.0) for vector in vectors] == [0, 1, 2]
    assert await provider.embed_documents([]) == []


@pytest.mark.parametrize(
    ("token_counts", "expected"),
    [
        ([4, 4, 4], [["a", "b"], ["c"]]),
        ([1, 1, 1], [["a", "b", "c"]]),
        ([10, 1, 10], [["a"], ["b"], ["c"]]),
    ],
)
def test_batches_respect_the_token_ceiling(token_counts, expected) -> None:
    assert pack_batches(["a", "b", "c"], token_counts, max_inputs=10, max_tokens=10) == expected


def test_batches_without_a_token_ceiling_only_follow_the_input_limit() -> None:
    assert pack_batches(["a", "b", "c"], None, max_inputs=2, max_tokens=None) == [
        ["a", "b"],
        ["c"],
    ]
    assert pack_batches([], None, max_inputs=2, max_tokens=None) == []


def test_a_chunk_over_the_token_ceiling_fails() -> None:
    with pytest.raises(ValueError, match="11 tokens exceeds the 10-token request limit"):
        pack_batches(["a"], [11], max_inputs=10, max_tokens=10)


@pytest.mark.asyncio
async def test_documents_are_split_by_tokens_before_the_request_limit(monkeypatch) -> None:
    client = install_fake_voyageai(
        monkeypatch,
        embedding_response([1.0] + [0.0] * 1023),
        embedding_response([1.0] + [0.0] * 1023, [1.0] + [0.0] * 1023),
    )
    provider = VoyageEmbeddingProvider("voyage-4", batch_size=3, request_concurrency=1)
    # The fake tokenizer counts one token per character.
    provider._model = dataclasses.replace(provider._model, max_request_tokens=5)

    await provider.embed_documents(["aaaa", "bb", "cc"])

    instance = client.instances[0]
    assert instance.tokenized == [["aaaa", "bb", "cc"]]
    assert [call[1][0] for call in instance.calls] == [["aaaa"], ["bb", "cc"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["voyage-4", "voyage-code-4", "voyage-context-4"])
async def test_only_multi_input_batches_are_tokenized(monkeypatch, model: str) -> None:
    unit = [1.0] + [0.0] * 1023
    client = install_fake_voyageai(
        monkeypatch,
        contextualized_response([unit]) if "context" in model else embedding_response(unit),
        contextualized_response([unit], [unit])
        if "context" in model
        else embedding_response(unit, unit),
    )
    provider = VoyageEmbeddingProvider(model)

    await provider.embed_query("a")
    assert client.instances[0].tokenized == []
    await provider.embed_documents(["a", "b"])
    assert client.instances[0].tokenized == [["a", "b"]]


@pytest.mark.asyncio
async def test_unavailable_tokenizer_falls_back_to_input_count_batching(monkeypatch) -> None:
    unit = [1.0] + [0.0] * 1023
    client = install_fake_voyageai(
        monkeypatch, embedding_response(unit, unit), embedding_response(unit)
    )
    provider = VoyageEmbeddingProvider("voyage-4", batch_size=2, request_concurrency=1)

    def broken_tokenize(self, texts, model):
        raise OSError("offline")

    monkeypatch.setattr(client, "tokenize", broken_tokenize)

    vectors = await provider.embed_documents(["a", "b", "c"])

    assert [call[1][0] for call in client.instances[0].calls] == [["a", "b"], ["c"]]
    assert len(vectors) == 3

    # The failure is remembered: later calls do not try the tokenizer again.
    attempts = 0

    def counting_tokenize(self, texts, model):
        nonlocal attempts
        attempts += 1
        raise OSError("offline")

    monkeypatch.setattr(client, "tokenize", counting_tokenize)
    FakeAsyncClient.responses.extend([embedding_response(unit, unit), embedding_response(unit)])
    await provider.embed_documents(["a", "b", "c"])
    assert attempts == 0


@pytest.mark.asyncio
async def test_vectors_are_unit_normalized(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch, embedding_response([3.0, 4.0] + [0.0] * 1022))

    vector = await VoyageEmbeddingProvider("voyage-4").embed_query("a")

    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0)


@pytest.mark.asyncio
async def test_contextualized_documents_use_auto_chunking_on_flat_strings(monkeypatch) -> None:
    client = install_fake_voyageai(
        monkeypatch,
        contextualized_response([[1.0] + [0.0] * 1023], [[0.0, 1.0] + [0.0] * 1022]),
    )
    provider = VoyageEmbeddingProvider("voyage-context-4")

    vectors = await provider.embed_documents(["a", "b"])

    endpoint, args, kwargs = client.instances[0].calls[0]
    assert endpoint == "contextualized_embed"
    assert args == (["a", "b"],)
    assert kwargs["input_type"] == "document"
    assert kwargs["enable_auto_chunking"] is True
    assert kwargs["chunk_size"] == 32_000
    assert [vector.index(1.0) for vector in vectors] == [0, 1]


@pytest.mark.asyncio
async def test_contextualized_query_is_not_auto_chunked(monkeypatch) -> None:
    client = install_fake_voyageai(monkeypatch, contextualized_response([[1.0] + [0.0] * 1023]))

    await VoyageEmbeddingProvider("voyage-context-3").embed_query("a")

    _, _, kwargs = client.instances[0].calls[0]
    assert kwargs["input_type"] == "query"
    assert "enable_auto_chunking" not in kwargs
    assert "chunk_size" not in kwargs


@pytest.mark.asyncio
async def test_contextualized_document_split_into_several_chunks_fails(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch, contextualized_response([[1.0] * 1024, [2.0] * 1024]))

    with pytest.raises(RuntimeError, match="expected one vector per Basic Memory chunk"):
        await VoyageEmbeddingProvider("voyage-context-4").embed_documents(["a"])


# --- Response and API failures ---


@pytest.mark.asyncio
async def test_vector_count_mismatch_fails(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch, embedding_response([1.0] * 1024))

    with pytest.raises(RuntimeError, match="returned 1 vectors for 2 inputs"):
        await VoyageEmbeddingProvider("voyage-4").embed_documents(["a", "b"])


@pytest.mark.asyncio
async def test_wrong_vector_size_fails(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch, embedding_response([1.0] * 8))

    with pytest.raises(RuntimeError, match="8-dimensional vectors"):
        await VoyageEmbeddingProvider("voyage-4").embed_query("a")


@pytest.mark.asyncio
async def test_api_error_reports_the_detail_field(monkeypatch) -> None:
    error = ERRORS.InvalidRequestError("sdk wording", json_body={"detail": "Model is unknown"})
    install_fake_voyageai(monkeypatch, error)

    with pytest.raises(RuntimeError, match="voyage-4.*Model is unknown") as raised:
        await VoyageEmbeddingProvider("voyage-4").embed_query("a")

    assert raised.value.__cause__ is error


def test_error_detail_falls_back_to_the_sdk_message_without_a_detail_body() -> None:
    assert voyage_error_detail(ERRORS.Timeout("timed out")) == "timed out"
    assert voyage_error_detail(ERRORS.Timeout("timed out", json_body={"other": 1})) == "timed out"
    assert voyage_error_detail(ERRORS.Timeout(None)) == "None"


@pytest.mark.asyncio
async def test_missing_api_key_fails(monkeypatch) -> None:
    install_fake_voyageai(monkeypatch)
    monkeypatch.delenv("VOYAGE_API_KEY")

    with pytest.raises(SemanticDependenciesMissingError, match="VOYAGE_API_KEY"):
        await VoyageEmbeddingProvider("voyage-4").embed_query("a")


@pytest.mark.asyncio
async def test_configured_key_wins_over_environment(monkeypatch) -> None:
    client = install_fake_voyageai(monkeypatch, embedding_response([1.0] * 1024))

    await VoyageEmbeddingProvider("voyage-4", api_key="configured").embed_query("a")

    assert client.instances[0].init_kwargs["api_key"] == "configured"


def test_missing_sdk_names_the_extra(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "voyageai", None)

    with pytest.raises(SemanticDependenciesMissingError, match=r"basic-memory\[voyage\]"):
        import_voyageai()


# --- Factory ---


def test_factory_builds_a_voyage_provider_with_stable_identity() -> None:
    config = BasicMemoryConfig(
        env="test",
        semantic_embedding_provider="voyage",
        semantic_embedding_model="voyage-4-large",
        semantic_embedding_dimensions=512,
    )

    provider = create_embedding_provider(config)

    assert isinstance(provider, VoyageEmbeddingProvider)
    assert provider.dimensions == 512
    assert create_embedding_provider(config) is provider
    assert (
        configured_embedding_provider_identity(config)
        == f"VoyageEmbeddingProvider:{provider.identity_key()}"
        == "VoyageEmbeddingProvider:voyage-4-large:512"
    )
    assert provider.runtime_log_attrs()["contextualized"] is False


def test_factory_identity_differs_per_model_and_dimension() -> None:
    def identity(model: str, dimensions: int | None) -> str:
        return configured_embedding_provider_identity(
            BasicMemoryConfig(
                env="test",
                semantic_embedding_provider="voyage",
                semantic_embedding_model=model,
                semantic_embedding_dimensions=dimensions,
            )
        )

    assert identity("voyage-4", None) == identity("voyage-4", 1024)
    assert (
        len({identity("voyage-4", 256), identity("voyage-4", 1024), identity("voyage-3.5", 1024)})
        == 3
    )


@pytest.mark.asyncio
async def test_unexpected_tokenizer_errors_are_not_swallowed(monkeypatch) -> None:
    client = install_fake_voyageai(monkeypatch)
    provider = VoyageEmbeddingProvider("voyage-4")

    def broken_tokenize(self, texts, model):
        raise TypeError("sdk contract changed")

    monkeypatch.setattr(client, "tokenize", broken_tokenize)

    with pytest.raises(TypeError, match="sdk contract changed"):
        await provider.embed_documents(["a", "b"])
