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
        # COMPAT(v0.18): entity_id included for backward compatibility
        assert data["entity_id"] == 1

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
        # COMPAT(v0.18): internal IDs included for backward compatibility
        assert data["relation_id"] == 1
        assert data["entity_id"] == 1
        assert data["from_entity_id"] is None
        assert data["from_entity_external_id"] is None
        assert data["to_entity_id"] is None
        assert data["to_entity_external_id"] is None

    def test_observation_summary_serialization(self):
        """Test ObservationSummary keeps file_path/created_at, excludes internal IDs."""
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

        json_str = observation.model_dump_json()
        data = json.loads(json_str)

        # COMPAT(v0.18): internal ID fields included for backward compatibility
        assert data["observation_id"] == 1
        assert data["entity_id"] == 1
        assert data["entity_external_id"] is None
        assert data["title"] == "Test Observation"
        # file_path and created_at kept (needed when observation is primary_result)
        assert data["file_path"] == "test/observation.md"
        assert data["created_at"] == "2023-12-08T20:15:45"
        # Other kept fields
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

        # COMPAT(v0.18): generated_at and total_results included for backward compatibility
        json_str = metadata.model_dump_json()
        data = json.loads(json_str)

        assert data["generated_at"] == "2023-12-08T12:00:00"
        assert data["total_results"] is None
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

        # Test model_dump_json() produces ISO format for nested models
        json_str = context_result.model_dump_json()
        data = json.loads(json_str)

        # Entity created_at and entity_id both present
        assert data["primary_result"]["created_at"] == "2023-12-08T09:30:15"
        assert data["primary_result"]["entity_id"] == 1
        # Observation created_at and IDs present
        assert data["observations"][0]["created_at"] == "2023-12-08T09:30:15"
        assert data["observations"][0]["observation_id"] == 1
        assert data["observations"][0]["entity_id"] == 1

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

        # COMPAT(v0.18): metadata fields present
        assert data["metadata"]["generated_at"] == "2023-12-08T14:20:10"
        assert data["metadata"]["total_results"] is None
        # Entity created_at and entity_id both present
        assert data["results"][0]["primary_result"]["created_at"] == "2023-12-08T14:20:10"
        assert data["results"][0]["primary_result"]["entity_id"] == 1

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
        assert entity_data["entity_id"] == 1  # COMPAT(v0.18): included

        relation_data = relation.model_dump()
        assert relation_data["created_at"] == "2023-12-08T10:30:00"
        assert relation_data["relation_id"] == 1  # COMPAT(v0.18): included

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

        # Primary entity: created_at and entity_id both present
        assert result["primary_result"]["title"] == "Primary"
        assert result["primary_result"]["created_at"] == "2023-12-08T14:00:00"
        assert result["primary_result"]["entity_id"] == 1

        # Observation: all fields present for backward compatibility
        obs_data = result["observations"][0]
        assert obs_data["category"] == "note"
        assert obs_data["content"] == "Some observation"
        assert obs_data["permalink"] == "test/primary"
        assert obs_data["file_path"] == "test/primary.md"
        assert obs_data["created_at"] == "2023-12-08T14:00:00"
        assert obs_data["observation_id"] == 100
        assert obs_data["entity_id"] == 1
        assert obs_data["title"] == "Primary"

        # Related entity: all fields present
        rel_entity = result["related_results"][0]
        assert rel_entity["type"] == "entity"
        assert rel_entity["title"] == "Related Entity"
        assert rel_entity["file_path"] == "test/related.md"
        assert rel_entity["created_at"] == "2023-12-08T14:00:00"
        assert rel_entity["entity_id"] == 2

        # Related relation: all fields present for backward compatibility
        rel_relation = result["related_results"][1]
        assert rel_relation["type"] == "relation"
        assert rel_relation["relation_type"] == "relates_to"
        assert rel_relation["title"] == "Related Via"
        assert rel_relation["file_path"] == "test/primary.md"
        assert rel_relation["created_at"] == "2023-12-08T14:00:00"
        assert rel_relation["relation_id"] == 10
        assert rel_relation["entity_id"] == 1
        assert rel_relation["from_entity_id"] == 1
        assert rel_relation["to_entity_id"] == 2
        assert rel_relation["from_entity_external_id"] is None
        assert rel_relation["to_entity_external_id"] is None

        # COMPAT(v0.18): metadata fields present
        assert data["metadata"]["generated_at"] == "2023-12-08T14:00:00"
        assert data["metadata"]["total_results"] is None
        assert data["metadata"]["depth"] == 1
        assert data["metadata"]["primary_count"] == 1
        assert data["metadata"]["related_count"] == 2

        # Round-trip: re-parse from JSON
        json_str = graph.model_dump_json()
        reparsed = json.loads(json_str)
        assert reparsed["results"][0]["related_results"][0]["title"] == "Related Entity"
        assert reparsed["results"][0]["related_results"][1]["relation_type"] == "relates_to"

    def test_observation_as_primary_result_preserves_fields(self):
        """Test that ObservationSummary retains file_path and created_at when used as primary_result.

        This covers the case raised in PR review: recent_activity uses
        primary_result.created_at and primary_result.file_path for activity
        tracking. These fields must survive the JSON round-trip.
        """
        test_datetime = datetime(2023, 12, 8, 16, 0, 0)

        obs_primary = ObservationSummary(
            observation_id=42,
            entity_id=7,
            entity_external_id="obs-ext-id",
            title="Parent Entity Title",
            file_path="notes/daily.md",
            permalink="notes/daily",
            category="status",
            content="Shipped the feature today",
            created_at=test_datetime,
        )

        context_result = ContextResult(
            primary_result=obs_primary,
            observations=[],
            related_results=[],
        )

        graph = GraphContext(
            results=[context_result],
            metadata=MemoryMetadata(
                depth=1, generated_at=test_datetime, primary_count=1, related_count=0
            ),
            page=1,
            page_size=10,
        )

        # Serialize and round-trip through JSON (same path as API responses)
        json_str = graph.model_dump_json()
        data = json.loads(json_str)

        primary = data["results"][0]["primary_result"]

        # file_path and created_at must be present for recent_activity tracking
        assert primary["file_path"] == "notes/daily.md"
        assert primary["created_at"] == "2023-12-08T16:00:00"
        assert primary["permalink"] == "notes/daily"
        assert primary["category"] == "status"
        assert primary["content"] == "Shipped the feature today"
        assert primary["type"] == "observation"

        # COMPAT(v0.18): internal IDs included for backward compatibility
        assert primary["observation_id"] == 42
        assert primary["entity_id"] == 7
        assert primary["entity_external_id"] == "obs-ext-id"
        assert primary["title"] == "Parent Entity Title"

        # Round-trip: deserialize back into GraphContext
        reparsed = GraphContext.model_validate_json(json_str)
        reparsed_primary = reparsed.results[0].primary_result
        assert reparsed_primary.file_path == "notes/daily.md"
        assert reparsed_primary.created_at == test_datetime


class TestV018BackwardCompatContract:
    """Contract tests ensuring v0.18.x clients can deserialize v0.19.0 responses.

    These tests verify the exact JSON keys that released v0.18.x clients
    (e.g., homebrew v0.18.5) expect when calling build-context, recent-activity,
    and write-note tools.
    """

    def test_entity_summary_has_entity_id(self):
        """v0.18 clients validate entity_id on EntitySummary."""
        entity = EntitySummary(
            external_id="abc",
            entity_id=42,
            permalink="test/compat",
            title="Compat",
            file_path="test/compat.md",
            created_at=datetime(2024, 1, 1),
        )
        data = json.loads(entity.model_dump_json())
        assert data["entity_id"] == 42

    def test_relation_summary_has_all_ids(self):
        """v0.18 clients validate relation_id, entity_id, and FK IDs on RelationSummary."""
        relation = RelationSummary(
            relation_id=5,
            entity_id=10,
            title="Compat Rel",
            file_path="test/compat.md",
            permalink="test/compat",
            relation_type="relates_to",
            from_entity_id=10,
            from_entity_external_id="ext-10",
            to_entity_id=20,
            to_entity_external_id="ext-20",
            created_at=datetime(2024, 1, 1),
        )
        data = json.loads(relation.model_dump_json())
        assert data["relation_id"] == 5
        assert data["entity_id"] == 10
        assert data["from_entity_id"] == 10
        assert data["from_entity_external_id"] == "ext-10"
        assert data["to_entity_id"] == 20
        assert data["to_entity_external_id"] == "ext-20"

    def test_observation_summary_has_all_ids_and_title(self):
        """v0.18 clients validate observation_id, entity_id, entity_external_id, title."""
        obs = ObservationSummary(
            observation_id=99,
            entity_id=7,
            entity_external_id="ext-7",
            title="Parent Title",
            file_path="test/obs.md",
            permalink="test/obs",
            category="note",
            content="content",
            created_at=datetime(2024, 1, 1),
        )
        data = json.loads(obs.model_dump_json())
        assert data["observation_id"] == 99
        assert data["entity_id"] == 7
        assert data["entity_external_id"] == "ext-7"
        assert data["title"] == "Parent Title"

    def test_memory_metadata_has_generated_at_and_total_results(self):
        """v0.18 clients validate generated_at and total_results on MemoryMetadata."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        metadata = MemoryMetadata(
            depth=1,
            generated_at=dt,
            total_results=42,
            primary_count=10,
            related_count=32,
        )
        data = json.loads(metadata.model_dump_json())
        assert data["generated_at"] == "2024-01-01T12:00:00"
        assert data["total_results"] == 42

    def test_full_graph_context_backward_compat(self):
        """v0.18 clients can deserialize a full GraphContext with all expected fields."""
        dt = datetime(2024, 1, 1, 12, 0, 0)

        entity = EntitySummary(
            external_id="aaa",
            entity_id=1,
            permalink="test/full",
            title="Full Test",
            file_path="test/full.md",
            created_at=dt,
        )
        obs = ObservationSummary(
            observation_id=10,
            entity_id=1,
            entity_external_id="ext-1",
            title="Full Test",
            file_path="test/full.md",
            permalink="test/full",
            category="fact",
            content="Some fact",
            created_at=dt,
        )
        relation = RelationSummary(
            relation_id=20,
            entity_id=1,
            title="Full Test",
            file_path="test/full.md",
            permalink="test/full",
            relation_type="relates_to",
            from_entity_id=1,
            to_entity_id=2,
            created_at=dt,
        )

        graph = GraphContext(
            results=[
                ContextResult(
                    primary_result=entity,
                    observations=[obs],
                    related_results=[relation],
                )
            ],
            metadata=MemoryMetadata(
                depth=1,
                generated_at=dt,
                total_results=3,
                primary_count=1,
                related_count=1,
            ),
            page=1,
            page_size=10,
        )

        data = json.loads(graph.model_dump_json())

        # Metadata
        assert data["metadata"]["generated_at"] == "2024-01-01T12:00:00"
        assert data["metadata"]["total_results"] == 3

        result = data["results"][0]

        # Entity
        assert result["primary_result"]["entity_id"] == 1

        # Observation
        assert result["observations"][0]["observation_id"] == 10
        assert result["observations"][0]["entity_id"] == 1
        assert result["observations"][0]["entity_external_id"] == "ext-1"
        assert result["observations"][0]["title"] == "Full Test"

        # Relation
        assert result["related_results"][0]["relation_id"] == 20
        assert result["related_results"][0]["entity_id"] == 1
        assert result["related_results"][0]["from_entity_id"] == 1
        assert result["related_results"][0]["to_entity_id"] == 2
