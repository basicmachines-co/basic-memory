"""Milvus configuration boundary tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.milvus_config import MilvusSettings


def test_settings_resolve_configured_values_before_fallback_aliases() -> None:
    settings = MilvusSettings.from_config(
        BasicMemoryConfig(
            env="test",
            milvus_uri=" https://zilliz.example ",
            milvus_token=" preferred ",
            milvus_collection_prefix="bm_vectors",
            milvus_database="knowledge",
            milvus_timeout_seconds=45.0,
        ),
        environ={
            "MILVUS_URI": "http://ignored",
            "MILVUS_TOKEN": "ignored",
        },
    )

    assert settings == MilvusSettings(
        uri="https://zilliz.example",
        token="preferred",
        collection_prefix="bm_vectors",
        database="knowledge",
        timeout_seconds=45.0,
    )


def test_settings_accept_milvus_fallback_aliases() -> None:
    settings = MilvusSettings.from_config(
        BasicMemoryConfig(env="test"),
        environ={
            "MILVUS_URI": " http://localhost:19530 ",
            "MILVUS_TOKEN": " root:Milvus ",
        },
    )

    assert settings == MilvusSettings(
        uri="http://localhost:19530",
        token="root:Milvus",
    )


def test_basic_memory_config_reads_milvus_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_MILVUS_URI", "https://zilliz.example")
    monkeypatch.setenv("BASIC_MEMORY_MILVUS_TOKEN", "secret")
    monkeypatch.setenv("BASIC_MEMORY_MILVUS_COLLECTION_PREFIX", "memory_vectors")
    monkeypatch.setenv("BASIC_MEMORY_MILVUS_DATABASE", "knowledge")
    monkeypatch.setenv("BASIC_MEMORY_MILVUS_TIMEOUT_SECONDS", "12.5")

    config = BasicMemoryConfig(env="test")

    assert config.milvus_uri == "https://zilliz.example"
    assert config.milvus_token == "secret"
    assert config.milvus_collection_prefix == "memory_vectors"
    assert config.milvus_database == "knowledge"
    assert config.milvus_timeout_seconds == 12.5


def test_settings_require_explicit_uri() -> None:
    with pytest.raises(ValueError, match="BASIC_MEMORY_MILVUS_URI"):
        MilvusSettings.from_config(BasicMemoryConfig(env="test"), environ={})


@pytest.mark.parametrize(
    ("create_settings", "message"),
    [
        (lambda: MilvusSettings(uri=" "), "URI cannot be empty"),
        (
            lambda: MilvusSettings(uri="db", collection_prefix="invalid-prefix"),
            "collection prefix",
        ),
        (lambda: MilvusSettings(uri="db", collection_prefix="9invalid"), "collection prefix"),
        (lambda: MilvusSettings(uri="db", collection_prefix="a" * 129), "cannot exceed"),
        (lambda: MilvusSettings(uri="db", database=" "), "database name"),
        (lambda: MilvusSettings(uri="db", timeout_seconds=0), "timeout"),
        (lambda: MilvusSettings(uri="db", timeout_seconds=float("inf")), "timeout"),
        (lambda: MilvusSettings(uri="db", timeout_seconds=float("nan")), "timeout"),
    ],
)
def test_settings_reject_invalid_values(
    create_settings: Callable[[], MilvusSettings],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        create_settings()
