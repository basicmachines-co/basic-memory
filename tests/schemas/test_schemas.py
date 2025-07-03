"""Tests for Pydantic schema validation and conversion."""

import pytest
from datetime import datetime, time, timedelta
from pydantic import ValidationError, BaseModel

from basic_memory.schemas import (
    Entity,
    EntityResponse,
    Relation,
    SearchNodesRequest,
    GetEntitiesRequest,
    RelationResponse,
)
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.schemas.base import to_snake_case, TimeFrame, parse_timeframe, validate_timeframe


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


def test_entity_response_with_none_permalink():
    """Test EntityResponse can handle None permalink (fixes issue #170).

    This test ensures that EntityResponse properly validates when the permalink
    field is None, which can occur when markdown files don't have explicit
    permalinks in their frontmatter during edit operations.
    """
    # Simulate database model attributes with None permalink
    db_data = {
        "title": "Test Entity",
        "permalink": None,  # This should not cause validation errors
        "file_path": "test/test-entity.md",
        "entity_type": "note",
        "content_type": "text/markdown",
        "observations": [],
        "relations": [],
        "created_at": "2023-01-01T00:00:00",
        "updated_at": "2023-01-01T00:00:00",
    }

    # This should not raise a ValidationError
    entity = EntityResponse.model_validate(db_data)
    assert entity.permalink is None
    assert entity.title == "Test Entity"
    assert entity.file_path == "test/test-entity.md"
    assert entity.entity_type == "note"
    assert len(entity.observations) == 0
    assert len(entity.relations) == 0


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


def test_edit_entity_request_validation():
    """Test EditEntityRequest validation for operation-specific parameters."""
    # Valid request - append operation
    edit_request = EditEntityRequest.model_validate(
        {"operation": "append", "content": "New content to append"}
    )
    assert edit_request.operation == "append"
    assert edit_request.content == "New content to append"

    # Valid request - find_replace operation with required find_text
    edit_request = EditEntityRequest.model_validate(
        {"operation": "find_replace", "content": "replacement text", "find_text": "text to find"}
    )
    assert edit_request.operation == "find_replace"
    assert edit_request.find_text == "text to find"

    # Valid request - replace_section operation with required section
    edit_request = EditEntityRequest.model_validate(
        {"operation": "replace_section", "content": "new section content", "section": "## Header"}
    )
    assert edit_request.operation == "replace_section"
    assert edit_request.section == "## Header"

    # Test that the validators return the value when validation passes
    # This ensures the `return v` statements are covered
    edit_request = EditEntityRequest.model_validate(
        {
            "operation": "find_replace",
            "content": "replacement",
            "find_text": "valid text",
            "section": "## Valid Section",
        }
    )
    assert edit_request.find_text == "valid text"  # Covers line 88 (return v)
    assert edit_request.section == "## Valid Section"  # Covers line 80 (return v)


def test_edit_entity_request_find_replace_empty_find_text():
    """Test that find_replace operation requires non-empty find_text parameter."""
    with pytest.raises(
        ValueError, match="find_text parameter is required for find_replace operation"
    ):
        EditEntityRequest.model_validate(
            {
                "operation": "find_replace",
                "content": "replacement text",
                "find_text": "",  # Empty string triggers validation
            }
        )


def test_edit_entity_request_replace_section_empty_section():
    """Test that replace_section operation requires non-empty section parameter."""
    with pytest.raises(
        ValueError, match="section parameter is required for replace_section operation"
    ):
        EditEntityRequest.model_validate(
            {
                "operation": "replace_section",
                "content": "new content",
                "section": "",  # Empty string triggers validation
            }
        )


# New tests for timeframe parsing functions
class TestTimeframeParsing:
    """Test cases for parse_timeframe() and validate_timeframe() functions."""

    def test_parse_timeframe_today(self):
        """Test that parse_timeframe('today') returns start of current day."""
        result = parse_timeframe("today")
        expected = datetime.combine(datetime.now().date(), time.min)

        assert result == expected
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_parse_timeframe_today_case_insensitive(self):
        """Test that parse_timeframe handles 'today' case-insensitively."""
        test_cases = ["today", "TODAY", "Today", "ToDay"]
        expected = datetime.combine(datetime.now().date(), time.min)

        for case in test_cases:
            result = parse_timeframe(case)
            assert result == expected

    def test_parse_timeframe_other_formats(self):
        """Test that parse_timeframe works with other dateparser formats."""
        now = datetime.now()

        # Test 1d ago - should be approximately 24 hours ago
        result_1d = parse_timeframe("1d")
        expected_1d = now - timedelta(days=1)
        diff = abs((result_1d - expected_1d).total_seconds())
        assert diff < 60  # Within 1 minute tolerance

        # Test yesterday - should be yesterday at same time
        result_yesterday = parse_timeframe("yesterday")
        # dateparser returns yesterday at current time, not start of yesterday
        assert result_yesterday.date() == (now.date() - timedelta(days=1))

        # Test 1 week ago
        result_week = parse_timeframe("1 week ago")
        expected_week = now - timedelta(weeks=1)
        diff = abs((result_week - expected_week).total_seconds())
        assert diff < 3600  # Within 1 hour tolerance

    def test_parse_timeframe_invalid(self):
        """Test that parse_timeframe raises ValueError for invalid input."""
        with pytest.raises(ValueError, match="Could not parse timeframe: invalid-timeframe"):
            parse_timeframe("invalid-timeframe")

        with pytest.raises(ValueError, match="Could not parse timeframe: not-a-date"):
            parse_timeframe("not-a-date")

    def test_validate_timeframe_preserves_special_cases(self):
        """Test that validate_timeframe preserves special timeframe strings."""
        # Should preserve 'today' as-is
        result = validate_timeframe("today")
        assert result == "today"

        # Should preserve case-normalized version
        result = validate_timeframe("TODAY")
        assert result == "today"

        result = validate_timeframe("Today")
        assert result == "today"

    def test_validate_timeframe_converts_regular_formats(self):
        """Test that validate_timeframe converts regular formats to duration."""
        # Test 1d format (should return as-is since it's already in standard format)
        result = validate_timeframe("1d")
        assert result == "1d"

        # Test other formats get converted to days
        result = validate_timeframe("yesterday")
        assert result == "1d"  # Yesterday is 1 day ago

        # Test week format
        result = validate_timeframe("1 week ago")
        assert result == "7d"  # 1 week = 7 days

    def test_validate_timeframe_error_cases(self):
        """Test that validate_timeframe raises appropriate errors."""
        # Invalid type
        with pytest.raises(ValueError, match="Timeframe must be a string"):
            validate_timeframe(123)  # type: ignore

        # Future timeframe
        with pytest.raises(ValueError, match="Timeframe cannot be in the future"):
            validate_timeframe("tomorrow")

        # Too far in past (>365 days)
        with pytest.raises(ValueError, match="Timeframe should be <= 1 year"):
            validate_timeframe("2 years ago")

        # Invalid format that can't be parsed
        with pytest.raises(ValueError, match="Could not parse timeframe"):
            validate_timeframe("not-a-real-timeframe")

    def test_timeframe_annotation_with_today(self):
        """Test that TimeFrame annotation works correctly with 'today'."""

        class TestModel(BaseModel):
            timeframe: TimeFrame

        # Should preserve 'today'
        model = TestModel(timeframe="today")
        assert model.timeframe == "today"

        # Should work with other formats
        model = TestModel(timeframe="1d")
        assert model.timeframe == "1d"

        model = TestModel(timeframe="yesterday")
        assert model.timeframe == "1d"

    def test_timeframe_integration_today_vs_1d(self):
        """Test the specific bug fix: 'today' vs '1d' behavior."""

        class TestModel(BaseModel):
            timeframe: TimeFrame

        # 'today' should be preserved
        today_model = TestModel(timeframe="today")
        assert today_model.timeframe == "today"

        # '1d' should also be preserved (it's already in standard format)
        oneday_model = TestModel(timeframe="1d")
        assert oneday_model.timeframe == "1d"

        # When parsed by parse_timeframe, they should be different
        today_parsed = parse_timeframe("today")
        oneday_parsed = parse_timeframe("1d")

        # 'today' should be start of today (00:00:00)
        assert today_parsed.hour == 0
        assert today_parsed.minute == 0

        # '1d' should be 24 hours ago (same time yesterday)
        now = datetime.now()
        expected_1d = now - timedelta(days=1)
        diff = abs((oneday_parsed - expected_1d).total_seconds())
        assert diff < 60  # Within 1 minute

        # They should be different times
        assert today_parsed != oneday_parsed
