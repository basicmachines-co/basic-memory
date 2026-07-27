"""PyMilvus client boundary tests."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

pytest.importorskip("pymilvus", reason="install basic-memory[milvus] to test Milvus")

from pymilvus import DataType
from pymilvus.exceptions import MilvusException

from basic_memory.repository.milvus_config import MilvusSettings
from basic_memory.repository.milvus_gateway import (
    MilvusStoredRecord,
    PyMilvusGateway,
    create_gateway,
)


class FakeSchema:
    def __init__(self) -> None:
        self.fields: list[dict[str, object]] = []

    def add_field(self, **field: object) -> None:
        self.fields.append(field)


class FakeIndexParams:
    def __init__(self) -> None:
        self.indexes: list[dict[str, object]] = []

    def add_index(self, **index: object) -> None:
        self.indexes.append(index)


class FakeIterator:
    def __init__(self, batches: Sequence[object]) -> None:
        self._batches = iter(batches)
        self.closed = False

    def next(self) -> object:
        return next(self._batches, [])

    def close(self) -> None:
        self.closed = True


def _valid_fields() -> list[dict[str, object]]:
    return [
        {
            "name": "id",
            "type": DataType.VARCHAR,
            "is_primary": True,
            "params": {"max_length": 64},
        },
        {"name": "entity_id", "type": DataType.INT64, "params": {}},
        {
            "name": "chunk_key",
            "type": DataType.VARCHAR,
            "params": {"max_length": 4096},
        },
        {
            "name": "source_hash",
            "type": DataType.VARCHAR,
            "params": {"max_length": 128},
        },
        {
            "name": "embedding",
            "type": DataType.FLOAT_VECTOR,
            "params": {"dim": 4},
        },
    ]


def _description(fields: object) -> dict[str, object]:
    return {
        "auto_id": False,
        "enable_dynamic_field": False,
        "fields": fields,
    }


def _fields_with(field_name: str, **updates: object) -> list[dict[str, object]]:
    fields = _valid_fields()
    field = next(field for field in fields if field["name"] == field_name)
    field.update(updates)
    return fields


class FakeClient:
    def __init__(self) -> None:
        self.has_collection_result = False
        self.description: object = _description(_valid_fields())
        self.index_names: object = ["embedding"]
        self.index_description: object = {
            "field_name": "embedding",
            "metric_type": "COSINE",
        }
        self.schema = FakeSchema()
        self.index_params = FakeIndexParams()
        self.created: list[dict[str, object]] = []
        self.create_exception: MilvusException | None = None
        self.upserts: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []
        self.iterator = FakeIterator([[{"id": "one"}, {"id": "two"}], []])
        self.search_result: object = [
            [
                {
                    "distance": 0.75,
                    "entity": {"entity_id": 7, "chunk_key": "summary:0"},
                }
            ]
        ]
        self.closed = False

    def has_collection(self, *, collection_name: str) -> bool:
        assert collection_name
        return self.has_collection_result

    def describe_collection(self, *, collection_name: str) -> object:
        assert collection_name
        return self.description

    def list_indexes(self, *, collection_name: str) -> object:
        assert collection_name
        return self.index_names

    def describe_index(self, *, collection_name: str, index_name: str) -> object:
        assert collection_name
        assert index_name
        return self.index_description

    def create_schema(self, **_kwargs: object) -> FakeSchema:
        return self.schema

    def prepare_index_params(self) -> FakeIndexParams:
        return self.index_params

    def create_collection(self, **kwargs: object) -> None:
        self.created.append(kwargs)
        if self.create_exception is not None:
            raise self.create_exception

    def upsert(self, **kwargs: object) -> None:
        self.upserts.append(kwargs)

    def delete(self, **kwargs: object) -> None:
        self.deletes.append(kwargs)

    def query_iterator(self, **_kwargs: object) -> FakeIterator:
        return self.iterator

    def search(self, **_kwargs: object) -> object:
        return self.search_result

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fake = FakeClient()
    monkeypatch.setattr(
        "basic_memory.repository.milvus_gateway.MilvusClient",
        lambda **_kwargs: fake,
    )
    return fake


@pytest.fixture
def gateway(client: FakeClient) -> PyMilvusGateway:
    return PyMilvusGateway(MilvusSettings(uri="http://localhost:19530", token="secret"))


def test_collection_dimensions_reports_missing_and_valid_collection(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    assert gateway.collection_dimensions("vectors") is None

    client.has_collection_result = True
    assert gateway.collection_dimensions("vectors") == 4


@pytest.mark.parametrize(
    ("description", "message"),
    [
        ([], "collection description"),
        (_description("invalid"), "collection fields"),
        (_description([[]]), "collection field"),
        (_description([{}]), "string name"),
        (_description([{"name": "id"}]), "missing required fields"),
        (
            _description(_fields_with("embedding", params=[])),
            "embedding field params",
        ),
        (
            _description(_fields_with("embedding", params={})),
            "does not report",
        ),
        (
            _description(_fields_with("embedding", params={"dim": []})),
            "invalid embedding dimensions",
        ),
        (
            _description([*_valid_fields(), {1: "invalid"}]),
            "field name",
        ),
    ],
)
def test_collection_dimensions_rejects_invalid_schema(
    gateway: PyMilvusGateway,
    client: FakeClient,
    description: object,
    message: str,
) -> None:
    client.has_collection_result = True
    client.description = description

    with pytest.raises(RuntimeError, match=message):
        gateway.collection_dimensions("vectors")


@pytest.mark.parametrize(
    ("description", "message"),
    [
        (
            _description(
                [
                    *_valid_fields(),
                    {"name": "extra", "type": DataType.INT64, "params": {}},
                ]
            ),
            "unexpected fields",
        ),
        (
            {**_description(_valid_fields()), "auto_id": True},
            "disable automatic IDs",
        ),
        (
            {**_description(_valid_fields()), "enable_dynamic_field": True},
            "disable dynamic fields",
        ),
        (
            _description(_fields_with("entity_id", type=DataType.VARCHAR)),
            "entity_id.*INT64",
        ),
        (
            _description(_fields_with("id", is_primary=False)),
            "must be the primary key",
        ),
        (
            _description(_fields_with("chunk_key", params={"max_length": 10})),
            "at least 4096",
        ),
    ],
)
def test_collection_dimensions_rejects_incompatible_definition(
    gateway: PyMilvusGateway,
    client: FakeClient,
    description: object,
    message: str,
) -> None:
    client.has_collection_result = True
    client.description = description

    with pytest.raises(RuntimeError, match=message):
        gateway.collection_dimensions("vectors")


@pytest.mark.parametrize(
    ("index_names", "index_description", "message"),
    [
        ({}, {}, "index names"),
        ([1], {}, "invalid index name"),
        (["embedding"], [], "index description"),
        ([], {}, "requires a COSINE index"),
        (
            ["embedding"],
            {"field_name": "embedding", "metric_type": "L2"},
            "requires a COSINE index",
        ),
    ],
)
def test_collection_dimensions_rejects_incompatible_index(
    gateway: PyMilvusGateway,
    client: FakeClient,
    index_names: object,
    index_description: object,
    message: str,
) -> None:
    client.has_collection_result = True
    client.index_names = index_names
    client.index_description = index_description

    with pytest.raises(RuntimeError, match=message):
        gateway.collection_dimensions("vectors")


def test_create_collection(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    assert gateway.create_collection("vectors", 4)

    assert [field["field_name"] for field in client.schema.fields] == [
        "id",
        "entity_id",
        "chunk_key",
        "source_hash",
        "embedding",
    ]
    assert client.index_params.indexes == [
        {
            "field_name": "embedding",
            "index_type": "AUTOINDEX",
            "metric_type": "COSINE",
        }
    ]
    assert client.created[0]["collection_name"] == "vectors"


def test_create_collection_reports_confirmed_race(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    client.create_exception = MilvusException(code=1, message="collection already exists")
    client.has_collection_result = True

    assert not gateway.create_collection("vectors", 4)


def test_create_collection_preserves_unconfirmed_error(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    client.create_exception = MilvusException(code=1, message="server failure")

    with pytest.raises(MilvusException, match="server failure"):
        gateway.create_collection("vectors", 4)


def test_upsert_maps_provider_record(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    gateway.upsert(
        "vectors",
        [
            MilvusStoredRecord(
                record_id="abc",
                entity_id=7,
                chunk_key="summary:0",
                source_hash="source-a",
                values=(1.0, 0.0),
            )
        ],
    )

    assert client.upserts == [
        {
            "collection_name": "vectors",
            "data": [
                {
                    "id": "abc",
                    "entity_id": 7,
                    "chunk_key": "summary:0",
                    "source_hash": "source-a",
                    "embedding": [1.0, 0.0],
                }
            ],
        }
    ]


def test_generation_delete_escapes_values_and_batches(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    records = [(f"id-{index}", f'hash-"{index}') for index in range(257)]

    gateway.delete_records("vectors", records)

    assert len(client.deletes) == 2
    first_filter = client.deletes[0]["filter"]
    assert isinstance(first_filter, str)
    assert 'source_hash == "hash-\\"0"' in first_filter


def test_delete_entity_and_ids(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    gateway.delete_entity("vectors", 7)
    gateway.delete_ids("vectors", [str(index) for index in range(257)])

    assert client.deletes[0] == {
        "collection_name": "vectors",
        "filter": "entity_id == 7",
    }
    assert len(client.deletes) == 3
    first_ids = client.deletes[1]["ids"]
    second_ids = client.deletes[2]["ids"]
    assert isinstance(first_ids, list)
    assert isinstance(second_ids, list)
    assert len(first_ids) == 256
    assert len(second_ids) == 1


def test_iter_ids_closes_iterator(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    assert list(gateway.iter_ids("vectors")) == ["one", "two"]
    assert client.iterator.closed


@pytest.mark.parametrize(
    ("batches", "message"),
    [
        (["invalid"], "query iterator batch"),
        ([[[]]], "query iterator record"),
        ([[{}]], "string id"),
    ],
)
def test_iter_ids_rejects_invalid_payload_and_closes(
    gateway: PyMilvusGateway,
    client: FakeClient,
    batches: Sequence[object],
    message: str,
) -> None:
    client.iterator = FakeIterator(batches)

    with pytest.raises(RuntimeError, match=message):
        list(gateway.iter_ids("vectors"))

    assert client.iterator.closed


def test_search_maps_nested_milvus_hits(
    gateway: PyMilvusGateway,
) -> None:
    matches = gateway.search("vectors", [1.0, 0.0], 5)

    assert matches[0].entity_id == 7
    assert matches[0].chunk_key == "summary:0"
    assert matches[0].score == 0.75


def test_search_accepts_flat_score_hits(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    client.search_result = [[{"score": 0.5, "entity_id": 8, "chunk_key": "summary:1"}]]

    matches = gateway.search("vectors", [1.0, 0.0], 5)

    assert matches[0].entity_id == 8
    assert matches[0].score == 0.5


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({}, "search results"),
        ([{}], "search hits"),
        ([[[]]], "search hit"),
        ([[{"entity": []}]], "search entity"),
        ([[{"distance": 0.5, "entity": {}}]], "vector key"),
        (
            [[{"distance": "bad", "entity": {"entity_id": 1, "chunk_key": "key"}}]],
            "numeric COSINE",
        ),
    ],
)
def test_search_rejects_invalid_payload(
    gateway: PyMilvusGateway,
    client: FakeClient,
    result: object,
    message: str,
) -> None:
    client.search_result = result

    with pytest.raises(RuntimeError, match=message):
        gateway.search("vectors", [1.0, 0.0], 5)


def test_search_handles_empty_results(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    client.search_result = []

    assert gateway.search("vectors", [1.0, 0.0], 5) == []


def test_close_releases_client(
    gateway: PyMilvusGateway,
    client: FakeClient,
) -> None:
    gateway.close()

    assert client.closed


def test_create_gateway_supports_tokenless_connections(client: FakeClient) -> None:
    gateway = create_gateway(MilvusSettings(uri="http://localhost:19530"))

    gateway.close()

    assert client.closed
