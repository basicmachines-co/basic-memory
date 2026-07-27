"""Validated configuration for the first-party Milvus vector index."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from basic_memory.config import BasicMemoryConfig

_COLLECTION_PREFIX_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_COLLECTION_PREFIX_LENGTH = 128


def _nonempty(value: str | None) -> str | None:
    if value and value.strip():
        return value.strip()
    return None


def _first_nonempty(source: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        if value := _nonempty(source.get(name)):
            return value
    return None


@dataclass(frozen=True, slots=True)
class MilvusSettings:
    """Connection and collection settings for the selected Milvus index."""

    uri: str
    token: str | None = None
    collection_prefix: str = "basic_memory"
    database: str = "default"

    def __post_init__(self) -> None:
        if not self.uri.strip():
            raise ValueError("Milvus URI cannot be empty.")
        if not _COLLECTION_PREFIX_PATTERN.fullmatch(self.collection_prefix):
            raise ValueError(
                "Milvus collection prefix must start with a letter or underscore and contain "
                "only letters, numbers, and underscores."
            )
        if len(self.collection_prefix) > _MAX_COLLECTION_PREFIX_LENGTH:
            raise ValueError(
                "Milvus collection prefix cannot exceed "
                f"{_MAX_COLLECTION_PREFIX_LENGTH} characters."
            )
        if not self.database.strip():
            raise ValueError("Milvus database name cannot be empty.")

    @classmethod
    def from_config(
        cls,
        app_config: BasicMemoryConfig,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> MilvusSettings:
        """Resolve validated settings once at the vector-index composition root."""
        source = os.environ if environ is None else environ
        uri = _nonempty(app_config.milvus_uri) or _first_nonempty(source, "MILVUS_URI")
        if uri is None:
            raise ValueError(
                "Milvus requires BASIC_MEMORY_MILVUS_URI (or MILVUS_URI). "
                "Use a server/Zilliz endpoint, or install 'basic-memory[milvus]' "
                "and provide a local database path."
            )
        token = _nonempty(app_config.milvus_token) or _first_nonempty(source, "MILVUS_TOKEN")
        return cls(
            uri=uri,
            token=token,
            collection_prefix=app_config.milvus_collection_prefix,
            database=app_config.milvus_database,
        )
