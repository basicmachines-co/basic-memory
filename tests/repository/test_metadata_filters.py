"""Tests for structured metadata filter parsing helpers."""

from datetime import date

import pytest

from basic_memory.repository.metadata_filters import (
    ParsedMetadataFilter,
    _is_numeric_collection,
    _is_numeric_value,
    build_postgres_json_path,
    build_sqlite_json_path,
    parse_metadata_filters,
)


def test_parse_simple_equality():
    parsed = parse_metadata_filters({"status": "in-progress"})
    assert parsed == [ParsedMetadataFilter(["status"], "eq", "in-progress")]


def test_parse_contains_list():
    parsed = parse_metadata_filters({"tags": ["security", "oauth"]})
    assert parsed == [ParsedMetadataFilter(["tags"], "contains", ["security", "oauth"])]


def test_parse_in_operator():
    parsed = parse_metadata_filters({"priority": {"$in": ["high", "critical"]}})
    assert parsed == [ParsedMetadataFilter(["priority"], "in", ["high", "critical"])]


def test_parse_comparison_numeric():
    parsed = parse_metadata_filters({"schema.confidence": {"$gt": 0.7}})
    assert parsed == [ParsedMetadataFilter(["schema", "confidence"], "gt", 0.7, "numeric")]


def test_parse_between_numeric():
    parsed = parse_metadata_filters({"score": {"$between": [0.3, 0.6]}})
    assert parsed == [ParsedMetadataFilter(["score"], "between", [0.3, 0.6], "numeric")]


def test_parse_between_text():
    parsed = parse_metadata_filters({"window": {"$between": ["2024-01-01", "2024-12-31"]}})
    assert parsed == [
        ParsedMetadataFilter(["window"], "between", ["2024-01-01", "2024-12-31"], "text")
    ]


def test_parse_normalizes_scalar_types():
    parsed = parse_metadata_filters({"flag": True, "created": date(2025, 1, 10), "ratio": 0.5})
    values = {f.path_parts[0]: f.value for f in parsed}
    assert values["flag"] == "True"
    assert values["created"] == "2025-01-10"
    assert values["ratio"] == "0.5"


def test_invalid_filter_key():
    with pytest.raises(ValueError):
        parse_metadata_filters({"bad key": "value"})


def test_invalid_operator():
    with pytest.raises(ValueError):
        parse_metadata_filters({"priority": {"$nope": "high"}})


def test_empty_list_rejected():
    with pytest.raises(ValueError):
        parse_metadata_filters({"tags": []})


def test_numeric_helpers():
    assert _is_numeric_value("1.5")
    assert _is_numeric_value(2)
    assert not _is_numeric_value("not-a-number")
    assert _is_numeric_collection(["1", 2, 3.5])
    assert not _is_numeric_collection(["1", "nope"])


def test_build_json_paths():
    assert build_sqlite_json_path(["schema", "confidence"]) == '$."schema"."confidence"'
    assert build_postgres_json_path(["schema", "confidence"]) == "{schema,confidence}"
