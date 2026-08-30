"""Fast checks for the multilingual corpus and benchmark metric contract."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from basic_memory.config import DatabaseBackend
from basic_memory.schemas.search import SearchRetrievalMode

from semantic.multilingual_benchmark import (
    RetrievalObservation,
    benchmark_retrieval_mode,
    benchmark_storage_case,
    directory_size_bytes,
    embedding_benchmark_config,
    embedding_model_case,
    summarize_retrieval,
    vector_storage_size_bytes,
)
from semantic.multilingual_corpus import (
    MULTILINGUAL_CORPUS,
    MultilingualCorpus,
    MultilingualDocument,
    MultilingualQuery,
    RetrievalCaseKind,
)


def test_multilingual_corpus_covers_required_languages_and_behaviors() -> None:
    document_languages = {document.language for document in MULTILINGUAL_CORPUS.documents}
    assert {"en", "zh", "ja", "ko", "ar", "ru", "es", "th", "mixed"} <= document_languages
    assert {query.kind for query in MULTILINGUAL_CORPUS.queries} == set(RetrievalCaseKind)


def test_multilingual_corpus_rejects_unknown_relevance_judgment() -> None:
    document = MultilingualDocument("Known", "known", "en", "Known content")
    query = MultilingualQuery(
        "unknown-target",
        "Find the missing note",
        "en",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("missing",),
    )

    with pytest.raises(ValueError, match="unknown permalinks"):
        MultilingualCorpus("invalid", (document,), (query,))


def test_embedding_model_contract_preserves_e5_prefixes() -> None:
    model_case = embedding_model_case("multilingual-e5-large")
    storage_case = benchmark_storage_case("postgres")
    config = embedding_benchmark_config(model_case, storage_case)

    assert config.semantic_embedding_dimensions == 1024
    assert config.semantic_embedding_document_prefix == "passage: "
    assert config.semantic_embedding_query_prefix == "query: "
    assert config.semantic_embedding_threads == 1
    assert config.semantic_embedding_parallel == 1


@pytest.mark.parametrize("value", ["", "mysql", "POSTGRESQL"])
def test_benchmark_storage_case_rejects_unsupported_values(value: str) -> None:
    with pytest.raises(ValueError, match="sqlite.*postgres.*milvus"):
        benchmark_storage_case(value)


@pytest.mark.parametrize("value", ["fts", "", "semantic"])
def test_benchmark_retrieval_mode_rejects_non_semantic_modes(value: str) -> None:
    with pytest.raises(ValueError, match="vector.*hybrid"):
        benchmark_retrieval_mode(value)


def test_benchmark_parsers_accept_supported_values() -> None:
    sqlite = benchmark_storage_case("SQLITE")
    postgres = benchmark_storage_case("POSTGRES")
    milvus = benchmark_storage_case("MILVUS")

    assert (
        embedding_benchmark_config(
            embedding_model_case("bge-small-en"), sqlite
        ).semantic_vector_index
        == "pgvector"
    )
    assert postgres.database_backend is DatabaseBackend.POSTGRES
    assert postgres.vector_index_name == "pgvector"
    assert milvus.database_backend is DatabaseBackend.POSTGRES
    assert milvus.vector_index_name == "milvus"
    assert benchmark_retrieval_mode("HYBRID") is SearchRetrievalMode.HYBRID


def test_milvus_benchmark_requires_explicit_uri() -> None:
    with pytest.raises(ValueError, match="Milvus.*URI"):
        embedding_benchmark_config(
            embedding_model_case("bge-small-en"),
            benchmark_storage_case("milvus"),
        )


def test_directory_size_does_not_double_count_hardlinks(tmp_path) -> None:
    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"model-bytes")
    os.link(model_file, tmp_path / "snapshot-model.onnx")

    assert directory_size_bytes(tmp_path) == len(b"model-bytes")


@pytest.mark.asyncio
async def test_sqlite_vector_storage_excludes_unrelated_tables(sqlite_engine_factory) -> None:
    engine, _ = sqlite_engine_factory
    storage_case = benchmark_storage_case("sqlite")
    vector_bytes_before = await vector_storage_size_bytes(engine, storage_case)

    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated_payload (content BLOB NOT NULL)"))
        await connection.execute(
            text("INSERT INTO unrelated_payload (content) VALUES (zeroblob(1048576))")
        )

    assert await vector_storage_size_bytes(engine, storage_case) == vector_bytes_before


def test_retrieval_summary_separates_ranking_and_threshold_failures() -> None:
    relevant_query = MultilingualQuery(
        "relevant",
        "relevant query",
        "en",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("relevant",),
    )
    missed_query = MultilingualQuery(
        "missed",
        "missed query",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("missed",),
    )
    negative_query = MultilingualQuery(
        "negative",
        "negative query",
        "en",
        RetrievalCaseKind.NEGATIVE,
        (),
    )
    observations = (
        RetrievalObservation(relevant_query, ("relevant",), ("relevant",), 0.001, 0.002),
        RetrievalObservation(missed_query, ("wrong",), (), 0.003, 0.004),
        RetrievalObservation(negative_query, ("wrong",), ("wrong",), 0.005, 0.006),
    )

    summary = summarize_retrieval("test", observations)

    assert summary.recall_at_5 == 0.5
    assert summary.mrr_at_10 == 0.5
    assert summary.accepted_empty_rate == 0.5
    assert summary.wrong_top_rate == 0.5
    assert summary.negative_false_positive_rate == 1.0
    assert summary.ranking_p50_ms == 3.0
    assert summary.accepted_p95_ms == pytest.approx(5.8)
