"""Tests for datetime serialization in memory schema models."""

import json
from datetime import datetime


from basic_memory.schemas.memory import (
    EntitySummary,
    RelationSummary,
    ObservationSummary,
    MemoryMetadata,
    GraphContext,
    ContextResult,
)


class TestDateTimeSerialization:
    """Test datetime serialization for MCP schema compliance."""

    def test_entity_summary_datetime_serialization(self):
        """Test EntitySummary serializes datetime as ISO format string."""
        test_datetime = datetime(2023, 12, 8, 10, 30, 0)

        entity = EntitySummary(
            entity_id=1,
            permalink="test/entity",
            title="Test Entity",
            file_path="test/entity.md",
            created_at=test_datetime,
        )

        # Test model_dump_json() produces ISO format
        json_str = entity.model_dump_json()
        data = json.loads(json_str)

        assert data["created_at"] == "2023-12-08T10:30:00"
        assert data["type"] == "entity"
        assert data["title"] == "Test Entity"

    def test_relation_summary_datetime_serialization(self):
        """Test RelationSummary serializes datetime as ISO format string."""
        test_datetime = datetime(2023, 12, 8, 15, 45, 30)

        relation = RelationSummary(
            relation_id=1,
            entity_id=1,
            title="Test Relation",
            file_path="test/relation.md",
            permalink="test/relation",
            relation_type="relates_to",
            from_entity="entity1",
            to_entity="entity2",
            created_at=test_datetime,
        )

        # Test model_dump_json() produces ISO format
        json_str = relation.model_dump_json()
        data = json.loads(json_str)

        assert data["created_at"] == "2023-12-08T15:45:30"
        assert data["type"] == "relation"
        assert data["relation_type"] == "relates_to"

    def test_observation_summary_datetime_serialization(self):
        """Test ObservationSummary serializes datetime as ISO format string."""
        test_datetime = datetime(2023, 12, 8, 20, 15, 45)

        observation = ObservationSummary(
            observation_id=1,
            entity_id=1,
            title="Test Observation",
            file_path="test/observation.md",
            permalink="test/observation",
            category="note",
            content="Test content",
            created_at=test_datetime,
        )

        # Test model_dump_json() produces ISO format
        json_str = observation.model_dump_json()
        data = json.loads(json_str)

        assert data["created_at"] == "2023-12-08T20:15:45"
        assert data["type"] == "observation"
        assert data["category"] == "note"

    def test_memory_metadata_datetime_serialization(self):
        """Test MemoryMetadata serializes datetime as ISO format string."""
        test_datetime = datetime(2023, 12, 8, 12, 0, 0)

        metadata = MemoryMetadata(
            depth=2, generated_at=test_datetime, primary_count=5, related_count=3
        )

        # Test model_dump_json() produces ISO format
        json_str = metadata.model_dump_json()
        data = json.loads(json_str)

        assert data["generated_at"] == "2023-12-08T12:00:00"
        assert data["depth"] == 2
        assert data["primary_count"] == 5

    def test_context_result_with_datetime_serialization(self):
        """Test ContextResult with nested models serializes datetime correctly."""
        test_datetime = datetime(2023, 12, 8, 9, 30, 15)

        entity = EntitySummary(
            entity_id=1,
            permalink="test/entity",
            title="Test Entity",
            file_path="test/entity.md",
            created_at=test_datetime,
        )

        observation = ObservationSummary(
            observation_id=1,
            entity_id=1,
            title="Test Observation",
            file_path="test/observation.md",
            permalink="test/observation",
            category="note",
            content="Test content",
            created_at=test_datetime,
        )

        context_result = ContextResult(
            primary_result=entity, observations=[observation], related_results=[]
        )

        # Test model_dump_json() produces ISO format for nested models
        json_str = context_result.model_dump_json()
        data = json.loads(json_str)

        assert data["primary_result"]["created_at"] == "2023-12-08T09:30:15"
        assert data["observations"][0]["created_at"] == "2023-12-08T09:30:15"

    def test_graph_context_full_serialization(self):
        """Test full GraphContext serialization with all datetime fields."""
        test_datetime = datetime(2023, 12, 8, 14, 20, 10)

        entity = EntitySummary(
            entity_id=1,
            permalink="test/entity",
            title="Test Entity",
            file_path="test/entity.md",
            created_at=test_datetime,
        )

        metadata = MemoryMetadata(
            depth=1, generated_at=test_datetime, primary_count=1, related_count=0
        )

        context_result = ContextResult(primary_result=entity, observations=[], related_results=[])

        graph_context = GraphContext(
            results=[context_result], metadata=metadata, page=1, page_size=10
        )

        # Test full serialization
        json_str = graph_context.model_dump_json()
        data = json.loads(json_str)

        assert data["metadata"]["generated_at"] == "2023-12-08T14:20:10"
        assert data["results"][0]["primary_result"]["created_at"] == "2023-12-08T14:20:10"

    def test_datetime_with_microseconds_serialization(self):
        """Test datetime with microseconds serializes correctly."""
        test_datetime = datetime(2023, 12, 8, 10, 30, 0, 123456)

        entity = EntitySummary(
            entity_id=1,
            permalink="test/entity",
            title="Test Entity",
            file_path="test/entity.md",
            created_at=test_datetime,
        )

        json_str = entity.model_dump_json()
        data = json.loads(json_str)

        # Should include microseconds in ISO format
        assert data["created_at"] == "2023-12-08T10:30:00.123456"

    def test_mcp_schema_validation_compatibility(self):
        """Test that serialized datetime format is compatible with MCP schema validation."""
        test_datetime = datetime(2023, 12, 8, 10, 30, 0)

        entity = EntitySummary(
            entity_id=1,
            permalink="test/entity",
            title="Test Entity",
            file_path="test/entity.md",
            created_at=test_datetime,
        )

        # Serialize to JSON
        json_str = entity.model_dump_json()
        data = json.loads(json_str)

        # Verify the format matches expected MCP "date-time" format
        datetime_str = data["created_at"]

        # Should be parseable back to datetime (ISO format validation)
        parsed_datetime = datetime.fromisoformat(datetime_str)
        assert parsed_datetime == test_datetime

        # Should match the expected ISO format pattern
        assert "T" in datetime_str  # Contains date-time separator
        assert len(datetime_str) >= 19  # At least YYYY-MM-DDTHH:MM:SS format

    def test_all_models_have_datetime_serializers_configured(self):
        """Test that all memory schema models have datetime field serializers configured."""
        models_to_test = [
            (EntitySummary, "created_at"),
            (RelationSummary, "created_at"),
            (ObservationSummary, "created_at"),
            (MemoryMetadata, "generated_at"),
        ]

        for model_class, datetime_field in models_to_test:
            # Create a test instance with a datetime field
            test_datetime = datetime(2023, 12, 8, 10, 30, 0)

            if model_class == EntitySummary:
                instance = model_class(
                    entity_id=1,
                    permalink="test",
                    title="Test",
                    file_path="test.md",
                    created_at=test_datetime,
                )
            elif model_class == RelationSummary:
                instance = model_class(
                    relation_id=1,
                    entity_id=1,
                    title="Test",
                    file_path="test.md",
                    permalink="test",
                    relation_type="test",
                    created_at=test_datetime,
                )
            elif model_class == ObservationSummary:
                instance = model_class(
                    observation_id=1,
                    entity_id=1,
                    title="Test",
                    file_path="test.md",
                    permalink="test",
                    category="test",
                    content="Test",
                    created_at=test_datetime,
                )
            elif model_class == MemoryMetadata:
                instance = model_class(depth=1, generated_at=test_datetime)

            # Test that model_dump produces ISO format for datetime field
            data = instance.model_dump()
            assert data[datetime_field] == "2023-12-08T10:30:00"
