"""The memory-JSON boundary preserves supported aliases and rejects other shapes."""

import pytest

from basic_memory.schemas.memory_json import (
    MemoryJsonEntity,
    MemoryJsonInputError,
    MemoryJsonRelation,
    parse_memory_json_records,
)


def test_memory_json_records_preserve_legacy_aliases() -> None:
    records = parse_memory_json_records(
        [
            {"type": "entity", "entityName": "First", "observations": ["A fact"]},
            {"type": "entity", "id": "Second", "entityType": "person"},
            {"type": "relation", "from_id": "First", "to_id": "Second", "relation_type": "knows"},
        ]
    )
    assert isinstance(records[0], MemoryJsonEntity)
    assert records[0].name == "First"
    assert records[0].entity_type == "entity"
    assert isinstance(records[1], MemoryJsonEntity)
    assert records[1].name == "Second"
    assert records[1].entity_type == "person"
    assert isinstance(records[2], MemoryJsonRelation)
    assert records[2].source == "First"
    assert records[2].target == "Second"
    assert records[2].relation_type == "knows"


@pytest.mark.parametrize("source", [{}, None, [None], [[]], [{"type": "entity", "name": ""}]])
def test_memory_json_rejects_non_record_input_without_exposing_it(source: object) -> None:
    with pytest.raises(MemoryJsonInputError, match="Invalid memory-JSON"):
        parse_memory_json_records(source)
