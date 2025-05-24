"""Tests for Pydantic schema validation and conversion."""

import pytest
from pydantic import ValidationError, BaseModel

from basic_memory.schemas import (
    Entity,
    EntityResponse,
    Relation,
    SearchNodesRequest,
    GetEntitiesRequest,
    RelationResponse,
)
from basic_memory.schemas.base import to_snake_case, TimeFrame


def test_entity_project_name():
    """Test creating EntityIn with minimal required fields."""
    data = {"title": "Test Entity", "folder": "test", "entity_type": "knowledge"}
    entity = Entity.model_validate(data)
    assert entity.file_path == "test/Test Entity.md"
    assert entity.permalink == "test/test-entity"
    assert entity.entity_type == "knowledge"


def test_entity_project_id():
    """Test creating EntityIn with minimal required fields."""
    data = {"project": 2, "title": "Test Entity", "folder": "test", "entity_type": "knowledge"}
    entity = Entity.model_validate(data)
    assert entity.file_path == "test/Test Entity.md"
    assert entity.permalink == "test/test-entity"
    assert entity.entity_type == "knowledge"


def test_entity_non_markdown():
    """Test entity for regular non-markdown file."""
    data = {
        "title": "Test Entity.txt",
        "folder": "test",
        "entity_type": "file",
        "content_type": "text/plain",
    }
    entity = Entity.model_validate(data)
    assert entity.file_path == "test/Test Entity.txt"
    assert entity.permalink == "test/test-entity"
    assert entity.entity_type == "file"


def test_entity_in_validation():
    """Test validation errors for EntityIn."""
    with pytest.raises(ValidationError):
        Entity.model_validate({"entity_type": "test"})  # Missing required fields


def test_relation_in_validation():
    """Test RelationIn validation."""
    data = {"from_id": "test/123", "to_id": "test/456", "relation_type": "test"}
    relation = Relation.model_validate(data)
    assert relation.from_id == "test/123"
    assert relation.to_id == "test/456"
    assert relation.relation_type == "test"
    assert relation.context is None

    # With context
    data["context"] = "test context"
    relation = Relation.model_validate(data)
    assert relation.context == "test context"

    # Missing required fields
    with pytest.raises(ValidationError):
        Relation.model_validate({"from_id": "123", "to_id": "456"})  # Missing relationType


def test_relation_response():
    """Test RelationResponse validation."""
    data = {
        "permalink": "test/123/relates_to/test/456",
        "from_id": "test/123",
        "to_id": "test/456",
        "relation_type": "relates_to",
        "from_entity": {"permalink": "test/123"},
        "to_entity": {"permalink": "test/456"},
    }
    relation = RelationResponse.model_validate(data)
    assert relation.from_id == "test/123"
    assert relation.to_id == "test/456"
    assert relation.relation_type == "relates_to"
    assert relation.context is None


def test_entity_out_from_attributes():
    """Test EntityOut creation from database model attributes."""
    # Simulate database model attributes
    db_data = {
        "title": "Test Entity",
        "permalink": "test/test",
        "file_path": "test",
        "entity_type": "knowledge",
        "content_type": "text/markdown",
        "observations": [
            {
                "id": 1,
                "permalink": "permalink",
                "category": "note",
                "content": "test obs",
                "context": None,
            }
        ],
        "relations": [
            {
                "id": 1,
                "permalink": "test/test/relates_to/test/test",
                "from_id": "test/test",
                "to_id": "test/test",
                "relation_type": "relates_to",
                "context": None,
            }
        ],
        "created_at": "2023-01-01T00:00:00",
        "updated_at": "2023-01-01T00:00:00",
    }
    entity = EntityResponse.model_validate(db_data)
    assert entity.permalink == "test/test"
    assert len(entity.observations) == 1
    assert len(entity.relations) == 1


def test_search_nodes_input():
    """Test SearchNodesInput validation."""
    search = SearchNodesRequest.model_validate({"query": "test query"})
    assert search.query == "test query"

    with pytest.raises(ValidationError):
        SearchNodesRequest.model_validate({})  # Missing required query


def test_open_nodes_input():
    """Test OpenNodesInput validation."""
    open_input = GetEntitiesRequest.model_validate({"permalinks": ["test/test", "test/test2"]})
    assert len(open_input.permalinks) == 2

    # Empty names list should fail
    with pytest.raises(ValidationError):
        GetEntitiesRequest.model_validate({"permalinks": []})


def test_path_sanitization():
    """Test to_snake_case() handles various inputs correctly."""
    test_cases = [
        ("BasicMemory", "basic_memory"),  # CamelCase
        ("Memory Service", "memory_service"),  # Spaces
        ("memory-service", "memory_service"),  # Hyphens
        ("Memory_Service", "memory_service"),  # Already has underscore
        ("API2Service", "api2_service"),  # Numbers
        ("  Spaces  ", "spaces"),  # Extra spaces
        ("mixedCase", "mixed_case"),  # Mixed case
        ("snake_case_already", "snake_case_already"),  # Already snake case
        ("ALLCAPS", "allcaps"),  # All caps
        ("with.dots", "with_dots"),  # Dots
    ]

    for input_str, expected in test_cases:
        result = to_snake_case(input_str)
        assert result == expected, f"Failed for input: {input_str}"


def test_permalink_generation():
    """Test permalink property generates correct paths."""
    test_cases = [
        ({"title": "BasicMemory", "folder": "test"}, "test/basic-memory"),
        ({"title": "Memory Service", "folder": "test"}, "test/memory-service"),
        ({"title": "API Gateway", "folder": "test"}, "test/api-gateway"),
        ({"title": "TestCase1", "folder": "test"}, "test/test-case1"),
        ({"title": "TestCaseRoot", "folder": ""}, "test-case-root"),
    ]

    for input_data, expected_path in test_cases:
        entity = Entity.model_validate(input_data)
        assert entity.permalink == expected_path, f"Failed for input: {input_data}"


@pytest.mark.parametrize(
    "timeframe,expected_valid",
    [
        ("7d", True),
        ("yesterday", True),
        ("2 days ago", True),
        ("last week", True),
        ("3 weeks ago", True),
        ("invalid", False),
        ("tomorrow", False),
        ("next week", False),
        ("", False),
        ("0d", True),
        ("366d", False),
        (1, False),
    ],
)
def test_timeframe_validation(timeframe: str, expected_valid: bool):
    """Test TimeFrame validation directly."""

    class TimeFrameModel(BaseModel):
        timeframe: TimeFrame

    if expected_valid:
        try:
            tf = TimeFrameModel.model_validate({"timeframe": timeframe})
            assert isinstance(tf.timeframe, str)
        except ValueError as e:
            pytest.fail(f"TimeFrame failed to validate '{timeframe}' with error: {e}")
    else:
        with pytest.raises(ValueError):
            tf = TimeFrameModel.model_validate({"timeframe": timeframe})
