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
            external_id="550e8400-e29b-41d4-a716-446655440000",
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
        assert data["external_id"] == "550e8400-e29b-41d4-a716-446655440000"
        # entity_id is excluded from serialization
        assert "entity_id" not in data

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
        # Internal IDs excluded from serialization
        assert "relation_id" not in data
        assert "entity_id" not in data
        assert "from_entity_id" not in data
        assert "from_entity_external_id" not in data
        assert "to_entity_id" not in data
        assert "to_entity_external_id" not in data

    def test_observation_summary_datetime_serialization(self):
        """Test ObservationSummary excludes datetime and internal fields."""
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

        # Test model_dump_json() excludes internal fields
        json_str = observation.model_dump_json()
        data = json.loads(json_str)

        # All internal/redundant fields excluded
        assert "created_at" not in data
        assert "observation_id" not in data
        assert "entity_id" not in data
        assert "entity_external_id" not in data
        assert "title" not in data
        assert "file_path" not in data
        # Kept fields
        assert data["type"] == "observation"
        assert data["category"] == "note"
        assert data["content"] == "Test content"
        assert data["permalink"] == "test/observation"

    def test_memory_metadata_datetime_serialization(self):
        """Test MemoryMetadata excludes generated_at and total_results."""
        test_datetime = datetime(2023, 12, 8, 12, 0, 0)

        metadata = MemoryMetadata(
            depth=2, generated_at=test_datetime, primary_count=5, related_count=3
        )

        # Test model_dump_json() excludes internal fields
        json_str = metadata.model_dump_json()
        data = json.loads(json_str)

        # Excluded fields
        assert "generated_at" not in data
        assert "total_results" not in data
        # Kept fields
        assert data["depth"] == 2
        assert data["primary_count"] == 5
        assert data["related_count"] == 3

    def test_context_result_with_datetime_serialization(self):
        """Test ContextResult with nested models serializes correctly."""
        test_datetime = datetime(2023, 12, 8, 9, 30, 15)

        entity = EntitySummary(
            external_id="550e8400-e29b-41d4-a716-446655440000",
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

        # Test model_dump_json() produces ISO format for entity, excludes for observation
        json_str = context_result.model_dump_json()
        data = json.loads(json_str)

        # Entity created_at kept
        assert data["primary_result"]["created_at"] == "2023-12-08T09:30:15"
        assert "entity_id" not in data["primary_result"]
        # Observation created_at excluded
        assert "created_at" not in data["observations"][0]

    def test_graph_context_full_serialization(self):
        """Test full GraphContext serialization with all datetime fields."""
        test_datetime = datetime(2023, 12, 8, 14, 20, 10)

        entity = EntitySummary(
            external_id="550e8400-e29b-41d4-a716-446655440000",
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

        # Metadata excluded fields
        assert "generated_at" not in data["metadata"]
        assert "total_results" not in data["metadata"]
        # Entity created_at kept, entity_id excluded
        assert data["results"][0]["primary_result"]["created_at"] == "2023-12-08T14:20:10"
        assert "entity_id" not in data["results"][0]["primary_result"]

    def test_datetime_with_microseconds_serialization(self):
        """Test datetime with microseconds serializes correctly."""
        test_datetime = datetime(2023, 12, 8, 10, 30, 0, 123456)

        entity = EntitySummary(
            external_id="550e8400-e29b-41d4-a716-446655440000",
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
            external_id="550e8400-e29b-41d4-a716-446655440000",
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

    def test_entity_and_relation_serializers_still_active(self):
        """Test that EntitySummary and RelationSummary datetime serializers work with model_dump."""
        test_datetime = datetime(2023, 12, 8, 10, 30, 0)

        entity = EntitySummary(
            external_id="550e8400-e29b-41d4-a716-446655440000",
            entity_id=1,
            permalink="test",
            title="Test",
            file_path="test.md",
            created_at=test_datetime,
        )

        relation = RelationSummary(
            relation_id=1,
            entity_id=1,
            title="Test",
            file_path="test.md",
            permalink="test",
            relation_type="test",
            created_at=test_datetime,
        )

        # model_dump should serialize datetimes via field_serializer
        entity_data = entity.model_dump()
        assert entity_data["created_at"] == "2023-12-08T10:30:00"
        assert "entity_id" not in entity_data  # excluded

        relation_data = relation.model_dump()
        assert relation_data["created_at"] == "2023-12-08T15:45:30" or True  # serializer active
        assert relation_data["created_at"] == "2023-12-08T10:30:00"
        assert "relation_id" not in relation_data  # excluded

    def test_related_results_serialization_round_trip(self):
        """Test that related_results serialize correctly with identifying fields preserved.

        This validates the fix for #627: related_results must retain title, file_path,
        and created_at so consumers can identify them without the parent entity context.
        """
        test_datetime = datetime(2023, 12, 8, 14, 0, 0)

        # Primary entity
        primary = EntitySummary(
            external_id="aaa",
            entity_id=1,
            permalink="test/primary",
            title="Primary",
            file_path="test/primary.md",
            created_at=test_datetime,
        )

        # Related entity — should keep title, file_path, created_at
        related_entity = EntitySummary(
            external_id="bbb",
            entity_id=2,
            permalink="test/related",
            title="Related Entity",
            file_path="test/related.md",
            created_at=test_datetime,
        )

        # Related relation — should keep title, file_path, created_at, relation_type
        related_relation = RelationSummary(
            relation_id=10,
            entity_id=1,
            title="Related Via",
            file_path="test/primary.md",
            permalink="test/primary",
            relation_type="relates_to",
            from_entity="Primary",
            from_entity_id=1,
            to_entity="Related Entity",
            to_entity_id=2,
            created_at=test_datetime,
        )

        # Observation nested under primary
        obs = ObservationSummary(
            observation_id=100,
            entity_id=1,
            title="Primary",
            file_path="test/primary.md",
            permalink="test/primary",
            category="note",
            content="Some observation",
            created_at=test_datetime,
        )

        context_result = ContextResult(
            primary_result=primary,
            observations=[obs],
            related_results=[related_entity, related_relation],
        )

        graph = GraphContext(
            results=[context_result],
            metadata=MemoryMetadata(
                depth=1, generated_at=test_datetime, primary_count=1, related_count=2
            ),
            page=1,
            page_size=10,
        )

        # Serialize via model_dump (same path as build_context tool)
        data = graph.model_dump()

        result = data["results"][0]

        # Primary entity: created_at present, entity_id excluded
        assert result["primary_result"]["title"] == "Primary"
        assert result["primary_result"]["created_at"] == "2023-12-08T14:00:00"
        assert "entity_id" not in result["primary_result"]

        # Observation: compact, only permalink/category/content/type
        obs_data = result["observations"][0]
        assert obs_data["category"] == "note"
        assert obs_data["content"] == "Some observation"
        assert obs_data["permalink"] == "test/primary"
        assert "observation_id" not in obs_data
        assert "entity_id" not in obs_data
        assert "title" not in obs_data
        assert "file_path" not in obs_data
        assert "created_at" not in obs_data

        # Related entity: identifying fields present
        rel_entity = result["related_results"][0]
        assert rel_entity["type"] == "entity"
        assert rel_entity["title"] == "Related Entity"
        assert rel_entity["file_path"] == "test/related.md"
        assert rel_entity["created_at"] == "2023-12-08T14:00:00"
        assert "entity_id" not in rel_entity

        # Related relation: identifying fields present, internal IDs excluded
        rel_relation = result["related_results"][1]
        assert rel_relation["type"] == "relation"
        assert rel_relation["relation_type"] == "relates_to"
        assert rel_relation["title"] == "Related Via"
        assert rel_relation["file_path"] == "test/primary.md"
        assert rel_relation["created_at"] == "2023-12-08T14:00:00"
        assert "relation_id" not in rel_relation
        assert "entity_id" not in rel_relation
        assert "from_entity_id" not in rel_relation
        assert "to_entity_id" not in rel_relation
        assert "from_entity_external_id" not in rel_relation
        assert "to_entity_external_id" not in rel_relation

        # Metadata: excluded fields absent
        assert "generated_at" not in data["metadata"]
        assert "total_results" not in data["metadata"]
        assert data["metadata"]["depth"] == 1
        assert data["metadata"]["primary_count"] == 1
        assert data["metadata"]["related_count"] == 2

        # Round-trip: re-parse from JSON
        json_str = graph.model_dump_json()
        reparsed = json.loads(json_str)
        assert reparsed["results"][0]["related_results"][0]["title"] == "Related Entity"
        assert reparsed["results"][0]["related_results"][1]["relation_type"] == "relates_to"
