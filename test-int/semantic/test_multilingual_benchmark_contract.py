"""Fast checks for the multilingual corpus and benchmark metric contract."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlite_vec
from sqlalchemy import text

from basic_memory.config import DatabaseBackend
from basic_memory.models.search import (
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE,
    create_sqlite_search_vector_embeddings,
)
from basic_memory.repository.semantic_chunking import split_text_into_chunks
from basic_memory.repository.search_repository_base import SMALL_NOTE_CONTENT_LIMIT
from basic_memory.schemas.search import SearchRetrievalMode

from semantic.multilingual_benchmark import (
    RetrievalObservation,
    benchmark_retrieval_mode,
    benchmark_storage_case,
    directory_size_bytes,
    embedding_benchmark_config,
    embedding_model_case,
    model_cache_size_bytes,
    process_peak_rss_bytes,
    result_permalinks,
    summarize_retrieval,
    vector_storage_size_bytes,
)
from semantic import multilingual_benchmark
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


def test_chunk_boundary_document_places_relevant_text_after_first_chunk() -> None:
    document = next(
        document
        for document in MULTILINGUAL_CORPUS.documents
        if document.permalink == "multilingual/ko-long-retention-exception"
    )
    source_text = "\n\n".join((document.title, document.permalink, document.content))

    chunks = split_text_into_chunks(source_text)

    assert len(document.content) > SMALL_NOTE_CONTENT_LIMIT
    assert len(chunks) >= 2
    assert any("법적 보존 명령" in chunk for chunk in chunks[1:])


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


def test_model_cache_size_uses_configured_cache_directory(tmp_path) -> None:
    model_case = embedding_model_case("bge-small-en")
    model_root = tmp_path / "models--qdrant--bge-small-en-v1.5-onnx-q" / "snapshots" / "revision"
    model_root.mkdir(parents=True)
    (model_root / "model_optimized.onnx").write_bytes(b"configured-cache")
    benchmark_config = embedding_benchmark_config(
        model_case,
        benchmark_storage_case("sqlite"),
    ).model_copy(update={"semantic_embedding_cache_dir": str(tmp_path)})

    assert model_cache_size_bytes(model_case, benchmark_config) == len(b"configured-cache")


def test_model_cache_size_supports_fastembed_url_fallback(tmp_path) -> None:
    model_case = embedding_model_case("multilingual-e5-large")
    model_root = tmp_path / "fast-multilingual-e5-large"
    model_root.mkdir()
    (model_root / "model.onnx").write_bytes(b"url-fallback")
    benchmark_config = embedding_benchmark_config(
        model_case,
        benchmark_storage_case("sqlite"),
    ).model_copy(update={"semantic_embedding_cache_dir": str(tmp_path)})

    assert model_cache_size_bytes(model_case, benchmark_config) == len(b"url-fallback")


def test_model_cache_size_prefers_materialized_hugging_face_source(tmp_path) -> None:
    model_case = embedding_model_case("multilingual-e5-large")
    hugging_face_root = (
        tmp_path / "models--qdrant--multilingual-e5-large-onnx" / "snapshots" / "revision"
    )
    hugging_face_root.mkdir(parents=True)
    (hugging_face_root / "model.onnx").write_bytes(b"hugging-face")
    fallback_root = tmp_path / "fast-multilingual-e5-large"
    fallback_root.mkdir()
    (fallback_root / "stale.onnx").write_bytes(b"stale-fallback-is-larger")
    benchmark_config = embedding_benchmark_config(
        model_case,
        benchmark_storage_case("sqlite"),
    ).model_copy(update={"semantic_embedding_cache_dir": str(tmp_path)})

    assert model_cache_size_bytes(model_case, benchmark_config) == len(b"hugging-face")


def test_process_peak_rss_uses_windows_peak_working_set(monkeypatch) -> None:
    monkeypatch.setattr(
        multilingual_benchmark.psutil,
        "Process",
        lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(peak_wset=4096)),
    )

    assert process_peak_rss_bytes(platform_name="win32") == 4096


@pytest.mark.asyncio
async def test_sqlite_vector_storage_excludes_unrelated_tables(sqlite_engine_factory) -> None:
    engine, _ = sqlite_engine_factory
    storage_case = benchmark_storage_case("sqlite")

    async with engine.connect() as connection:
        dbstat_available = await connection.scalar(
            text("SELECT sqlite_compileoption_used('ENABLE_DBSTAT_VTAB')")
        )
    if not dbstat_available:
        pytest.skip("SQLite dbstat is required for physical vector-storage measurement")

    async with engine.begin() as connection:
        raw_connection = await connection.get_raw_connection()
        driver_connection = raw_connection.driver_connection
        try:
            await driver_connection.enable_load_extension(True)
        except AttributeError:
            pytest.skip(
                "SQLite loadable-extension support is required for physical vector measurement"
            )
        await driver_connection.load_extension(sqlite_vec.loadable_path())
        await driver_connection.enable_load_extension(False)
        await connection.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS)
        await connection.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY)
        await connection.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE)
        await connection.execute(create_sqlite_search_vector_embeddings(4))

    vector_bytes_before = await vector_storage_size_bytes(engine, storage_case)
    async with engine.connect() as connection:
        vector_relations = set(
            (
                await connection.execute(
                    text(
                        "SELECT name FROM sqlite_schema "
                        "WHERE tbl_name = 'search_vector_chunks' "
                        "OR tbl_name LIKE 'search_vector_embeddings%'"
                    )
                )
            ).scalars()
        )
        vector_relations.update(
            (
                await connection.execute(
                    text(
                        "SELECT DISTINCT name FROM dbstat "
                        "WHERE name = 'search_vector_chunks' "
                        "OR name LIKE 'search_vector_embeddings%'"
                    )
                )
            ).scalars()
        )
        dbstat_rows = await connection.execute(
            text("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name")
        )
        dbstat_bytes = dict(dbstat_rows.tuples().all())

    autoindexes = {
        name
        for name in vector_relations
        if name.startswith("sqlite_autoindex_search_vector_embeddings_")
    }
    vector_dbstat_relations = vector_relations.intersection(dbstat_bytes)
    assert autoindexes
    assert autoindexes <= vector_dbstat_relations
    assert vector_bytes_before == sum(dbstat_bytes[name] for name in vector_dbstat_relations)

    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated_payload (content BLOB NOT NULL)"))
        await connection.execute(
            text("INSERT INTO unrelated_payload (content) VALUES (zeroblob(1048576))")
        )

    assert await vector_storage_size_bytes(engine, storage_case) == vector_bytes_before


@pytest.mark.asyncio
async def test_milvus_vector_storage_includes_postgres_manifest(tmp_path, mocker) -> None:
    milvus_directory = tmp_path / "milvus"
    milvus_directory.mkdir()
    (milvus_directory / "vectors.db").write_bytes(b"milvus-vectors")
    relation_result = mocker.Mock()
    relation_result.scalar_one.return_value = 4096
    connection = mocker.Mock()
    connection.execute = AsyncMock(return_value=relation_result)
    engine = mocker.Mock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)

    measured_bytes = await vector_storage_size_bytes(
        engine,
        benchmark_storage_case("milvus"),
        milvus_storage_directory=milvus_directory,
    )

    assert measured_bytes == len(b"milvus-vectors") + 4096
    assert connection.execute.await_args is not None
    statement = str(connection.execute.await_args.args[0])
    assert "search_vector_chunks" in statement
    assert "search_vector_embeddings" not in statement


def test_chunk_boundary_results_preserve_rank_and_require_the_later_chunk(mocker) -> None:
    query = next(
        query for query in MULTILINGUAL_CORPUS.queries if query.name == "long-retention-korean"
    )
    distractor = mocker.Mock(
        permalink="multilingual/en-password-reset",
        matched_chunk_text="password reset",
    )
    first_chunk = mocker.Mock(
        permalink="multilingual/ko-long-retention-exception",
        matched_chunk_text="보관 정책 검토",
    )
    later_chunk = mocker.Mock(
        permalink="multilingual/ko-long-retention-exception",
        matched_chunk_text="법적 보존 명령이 적용된 고객 기록",
    )
    later_chunk_ranked_second = mocker.Mock(
        permalink="multilingual/ko-long-retention-exception",
        matched_chunk_text="보관 정책 검토\n---\n법적 보존 명령이 적용된 고객 기록",
    )

    assert result_permalinks([distractor, first_chunk], query) == (
        "multilingual/en-password-reset",
    )
    assert result_permalinks([distractor, later_chunk], query) == (
        "multilingual/en-password-reset",
        "multilingual/ko-long-retention-exception",
    )
    assert result_permalinks([distractor, later_chunk_ranked_second], query) == (
        "multilingual/en-password-reset",
    )


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
