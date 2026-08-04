"""Synchronous PyMilvus repository used from worker threads."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pymilvus import DataType, MilvusClient
from pymilvus.exceptions import MilvusException

from basic_memory.repository.milvus_config import MilvusSettings

_CHUNK_KEY_MAX_LENGTH = 4096
_SOURCE_HASH_MAX_LENGTH = 128
_DELETE_BATCH_SIZE = 256
_QUERY_BATCH_SIZE = 1_000
# The SQL manifest becomes ready when a write is acknowledged, so repository
# reads must observe that write even when they use a fresh PyMilvus client.
_CONSISTENCY_LEVEL = "Strong"
_REQUIRED_FIELDS = {"id", "entity_id", "chunk_key", "source_hash", "embedding"}
_FIELD_TYPES = {
    "id": DataType.VARCHAR,
    "entity_id": DataType.INT64,
    "chunk_key": DataType.VARCHAR,
    "source_hash": DataType.VARCHAR,
    "embedding": DataType.FLOAT_VECTOR,
}
_MIN_VARCHAR_LENGTHS = {
    "id": 64,
    "chunk_key": _CHUNK_KEY_MAX_LENGTH,
    "source_hash": _SOURCE_HASH_MAX_LENGTH,
}


@dataclass(frozen=True, slots=True)
class MilvusStoredRecord:
    """Milvus representation of one Basic Memory vector record."""

    record_id: str
    entity_id: int
    chunk_key: str
    source_hash: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class MilvusStoredMatch:
    """Raw COSINE result returned by Milvus."""

    entity_id: int
    chunk_key: str
    score: float


class MilvusRepository(Protocol):
    """Narrow blocking repository capability consumed by the async index."""

    def collection_dimensions(self, collection_name: str) -> int | None: ...

    def load_collection(self, collection_name: str) -> None: ...

    def create_collection(self, collection_name: str, dimensions: int) -> bool: ...

    def upsert(self, collection_name: str, records: Sequence[MilvusStoredRecord]) -> None: ...

    def delete_records(
        self,
        collection_name: str,
        records: Sequence[tuple[str, str]],
    ) -> None: ...

    def delete_entity(self, collection_name: str, entity_id: int) -> None: ...

    def iter_ids(self, collection_name: str) -> Iterator[str]: ...

    def delete_ids(self, collection_name: str, record_ids: Sequence[str]) -> None: ...

    def search(
        self,
        collection_name: str,
        query: Sequence[float],
        limit: int,
    ) -> list[MilvusStoredMatch]: ...

    def close(self) -> None: ...


def _string_literal(value: str) -> str:
    """Encode a Milvus filter string without allowing expression injection."""
    return json.dumps(value)


def _batches[T](values: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _require_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"Milvus returned an invalid {context} payload.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise RuntimeError(f"Milvus returned an invalid {context} field name.")
        result[key] = item
    return result


def _require_sequence(value: object, context: str) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    raise RuntimeError(f"Milvus returned an invalid {context} payload.")


class PyMilvusRepository:
    """Typed, explicit repository around the synchronous Milvus client."""

    def __init__(self, settings: MilvusSettings) -> None:
        self._timeout = settings.timeout_seconds
        if settings.token is None:
            self._client = MilvusClient(
                uri=settings.uri,
                db_name=settings.database,
                timeout=self._timeout,
            )
        else:
            self._client = MilvusClient(
                uri=settings.uri,
                token=settings.token,
                db_name=settings.database,
                timeout=self._timeout,
            )

    def collection_dimensions(self, collection_name: str) -> int | None:
        if not self._client.has_collection(
            collection_name=collection_name,
            timeout=self._timeout,
        ):
            return None

        description = _require_mapping(
            self._client.describe_collection(
                collection_name=collection_name,
                timeout=self._timeout,
            ),
            "collection description",
        )
        raw_fields = _require_sequence(description.get("fields"), "collection fields")
        fields: dict[str, Mapping[str, object]] = {}
        for raw_field in raw_fields:
            field = _require_mapping(raw_field, "collection field")
            name = field.get("name")
            if not isinstance(name, str):
                raise RuntimeError("Milvus collection field is missing a string name.")
            fields[name] = field

        missing_fields = sorted(_REQUIRED_FIELDS - fields.keys())
        if missing_fields:
            raise RuntimeError(
                f"Milvus collection '{collection_name}' is missing required fields: "
                f"{', '.join(missing_fields)}."
            )
        unexpected_fields = sorted(fields.keys() - _REQUIRED_FIELDS)
        if unexpected_fields:
            raise RuntimeError(
                f"Milvus collection '{collection_name}' has unexpected fields: "
                f"{', '.join(unexpected_fields)}."
            )
        if description.get("auto_id") is not False:
            raise RuntimeError(f"Milvus collection '{collection_name}' must disable automatic IDs.")
        if description.get("enable_dynamic_field") is not False:
            raise RuntimeError(
                f"Milvus collection '{collection_name}' must disable dynamic fields."
            )

        for field_name, expected_type in _FIELD_TYPES.items():
            if fields[field_name].get("type") != expected_type:
                raise RuntimeError(
                    f"Milvus collection '{collection_name}' field '{field_name}' must use "
                    f"{expected_type.name}."
                )
        if fields["id"].get("is_primary") is not True:
            raise RuntimeError(
                f"Milvus collection '{collection_name}' field 'id' must be the primary key."
            )

        for field_name, minimum_length in _MIN_VARCHAR_LENGTHS.items():
            params = _require_mapping(
                fields[field_name].get("params"),
                f"{field_name} field params",
            )
            max_length = params.get("max_length")
            if (
                not isinstance(max_length, int)
                or isinstance(max_length, bool)
                or max_length < minimum_length
            ):
                raise RuntimeError(
                    f"Milvus collection '{collection_name}' field '{field_name}' must allow "
                    f"at least {minimum_length} characters."
                )

        embedding_params = _require_mapping(
            fields["embedding"].get("params"),
            "embedding field params",
        )
        dimensions = embedding_params.get("dim")
        if dimensions is None:
            raise RuntimeError(
                f"Milvus collection '{collection_name}' does not report embedding dimensions."
            )
        if not isinstance(dimensions, int) or isinstance(dimensions, bool):
            raise RuntimeError(
                f"Milvus collection '{collection_name}' reports invalid embedding dimensions."
            )

        raw_index_names = _require_sequence(
            self._client.list_indexes(
                collection_name=collection_name,
                timeout=self._timeout,
            ),
            "index names",
        )
        has_cosine_index = False
        for raw_index_name in raw_index_names:
            if not isinstance(raw_index_name, str):
                raise RuntimeError("Milvus returned an invalid index name.")
            index = _require_mapping(
                self._client.describe_index(
                    collection_name=collection_name,
                    index_name=raw_index_name,
                    timeout=self._timeout,
                ),
                "index description",
            )
            if index.get("field_name") == "embedding" and index.get("metric_type") == "COSINE":
                has_cosine_index = True
        if not has_cosine_index:
            raise RuntimeError(
                f"Milvus collection '{collection_name}' requires a COSINE index on 'embedding'."
            )
        return dimensions

    def load_collection(self, collection_name: str) -> None:
        self._client.load_collection(
            collection_name=collection_name,
            timeout=self._timeout,
        )

    def create_collection(self, collection_name: str, dimensions: int) -> bool:
        """Return whether this client created the collection."""
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="id",
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=64,
        )
        schema.add_field(field_name="entity_id", datatype=DataType.INT64)
        schema.add_field(
            field_name="chunk_key",
            datatype=DataType.VARCHAR,
            max_length=_CHUNK_KEY_MAX_LENGTH,
        )
        schema.add_field(
            field_name="source_hash",
            datatype=DataType.VARCHAR,
            max_length=_SOURCE_HASH_MAX_LENGTH,
        )
        schema.add_field(
            field_name="embedding",
            datatype=DataType.FLOAT_VECTOR,
            dim=dimensions,
        )
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        try:
            self._client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params,
                consistency_level=_CONSISTENCY_LEVEL,
                timeout=self._timeout,
            )
        except MilvusException:
            # Trigger: another process may create the collection after our existence check.
            # Why: Milvus reports that race as the same broad exception used for other server
            # failures, so confirm resulting state before treating the create as successful.
            # Outcome: callers re-read and validate the winning collection's dimensions.
            if self._client.has_collection(
                collection_name=collection_name,
                timeout=self._timeout,
            ):
                return False
            raise
        return True

    def upsert(self, collection_name: str, records: Sequence[MilvusStoredRecord]) -> None:
        self._client.upsert(
            collection_name=collection_name,
            data=[
                {
                    "id": record.record_id,
                    "entity_id": record.entity_id,
                    "chunk_key": record.chunk_key,
                    "source_hash": record.source_hash,
                    "embedding": list(record.values),
                }
                for record in records
            ],
            timeout=self._timeout,
        )

    def delete_records(
        self,
        collection_name: str,
        records: Sequence[tuple[str, str]],
    ) -> None:
        for batch in _batches(records, _DELETE_BATCH_SIZE):
            predicates = [
                f"(id == {_string_literal(record_id)} "
                f"and source_hash == {_string_literal(source_hash)})"
                for record_id, source_hash in batch
            ]
            self._client.delete(
                collection_name=collection_name,
                filter=" or ".join(predicates),
                timeout=self._timeout,
            )

    def delete_entity(self, collection_name: str, entity_id: int) -> None:
        self._client.delete(
            collection_name=collection_name,
            filter=f"entity_id == {int(entity_id)}",
            timeout=self._timeout,
        )

    def iter_ids(self, collection_name: str) -> Iterator[str]:
        iterator = self._client.query_iterator(
            collection_name=collection_name,
            batch_size=_QUERY_BATCH_SIZE,
            limit=-1,
            filter="",
            output_fields=["id"],
            consistency_level=_CONSISTENCY_LEVEL,
            timeout=self._timeout,
        )
        try:
            while raw_batch := iterator.next():
                for raw_record in _require_sequence(raw_batch, "query iterator batch"):
                    record = _require_mapping(raw_record, "query iterator record")
                    record_id = record.get("id")
                    if not isinstance(record_id, str):
                        raise RuntimeError("Milvus query result is missing a string id.")
                    yield record_id
        finally:
            iterator.close()

    def delete_ids(self, collection_name: str, record_ids: Sequence[str]) -> None:
        for batch in _batches(record_ids, _DELETE_BATCH_SIZE):
            self._client.delete(
                collection_name=collection_name,
                ids=list(batch),
                timeout=self._timeout,
            )

    def search(
        self,
        collection_name: str,
        query: Sequence[float],
        limit: int,
    ) -> list[MilvusStoredMatch]:
        raw_results = _require_sequence(
            self._client.search(
                collection_name=collection_name,
                data=[list(query)],
                anns_field="embedding",
                limit=limit,
                search_params={"metric_type": "COSINE"},
                output_fields=["entity_id", "chunk_key"],
                consistency_level=_CONSISTENCY_LEVEL,
                timeout=self._timeout,
            ),
            "search results",
        )
        if not raw_results:
            return []
        raw_hits = _require_sequence(raw_results[0], "search hits")
        matches: list[MilvusStoredMatch] = []
        for raw_hit in raw_hits:
            hit = _require_mapping(raw_hit, "search hit")
            raw_entity = hit.get("entity", hit)
            entity = _require_mapping(raw_entity, "search entity")
            entity_id = entity.get("entity_id")
            chunk_key = entity.get("chunk_key")
            score = hit.get("distance", hit.get("score"))
            if not isinstance(entity_id, int) or not isinstance(chunk_key, str):
                raise RuntimeError("Milvus search hit is missing its Basic Memory vector key.")
            if not isinstance(score, (int, float)):
                raise RuntimeError("Milvus search hit is missing a numeric COSINE score.")
            matches.append(
                MilvusStoredMatch(
                    entity_id=entity_id,
                    chunk_key=chunk_key,
                    score=float(score),
                )
            )
        return matches

    def close(self) -> None:
        self._client.close()


def create_repository(settings: MilvusSettings) -> MilvusRepository:
    """Create the concrete repository at the first-party provider boundary."""
    return PyMilvusRepository(settings)
