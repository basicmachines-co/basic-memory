"""Tests for basic_memory.schema.diff -- schema drift detection."""

from basic_memory.schema.diff import SchemaDrift, diff_schema
from basic_memory.schema.inference import NoteData
from basic_memory.schema.parser import SchemaDefinition, SchemaField


# --- Helpers ---


def _make_schema(
    fields: list[SchemaField],
    entity: str = "Person",
) -> SchemaDefinition:
    return SchemaDefinition(entity=entity, version=1, fields=fields, validation_mode="warn")


def _scalar_field(
    name: str,
    required: bool = True,
    is_array: bool = False,
) -> SchemaField:
    return SchemaField(name=name, type="string", required=required, is_array=is_array)


def _entity_ref_field(
    name: str,
    required: bool = True,
    is_array: bool = False,
) -> SchemaField:
    return SchemaField(
        name=name, type="Organization", required=required,
        is_entity_ref=True, is_array=is_array,
    )


def _note(
    identifier: str,
    observations: list[tuple[str, str]] | None = None,
    relations: list[tuple[str, str]] | None = None,
) -> NoteData:
    return NoteData(
        identifier=identifier,
        observations=observations or [],
        relations=relations or [],
    )


# --- No drift ---


class TestDiffNoDrift:
    def test_perfect_match(self):
        schema = _make_schema([_scalar_field("name"), _scalar_field("role")])
        notes = [
            _note("n0", observations=[("name", "Alice"), ("role", "Eng")]),
            _note("n1", observations=[("name", "Bob"), ("role", "PM")]),
        ]
        drift = diff_schema(schema, notes)

        assert drift.new_fields == []
        assert drift.dropped_fields == []
        assert drift.cardinality_changes == []


# --- Empty notes ---


class TestDiffEmptyNotes:
    def test_empty_notes_returns_empty_drift(self):
        schema = _make_schema([_scalar_field("name")])
        drift = diff_schema(schema, [])
        assert isinstance(drift, SchemaDrift)
        assert drift.new_fields == []
        assert drift.dropped_fields == []
        assert drift.cardinality_changes == []


# --- New fields ---


class TestDiffNewFields:
    def test_new_field_detected(self):
        """Field common in notes but not in schema -> new field."""
        schema = _make_schema([_scalar_field("name")])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}"), ("role", f"R{i}")])
            for i in range(4)
        ]
        drift = diff_schema(schema, notes)

        assert len(drift.new_fields) == 1
        assert drift.new_fields[0].name == "role"

    def test_new_field_below_threshold_not_reported(self):
        """Field in notes but below new_field_threshold -> not reported."""
        schema = _make_schema([_scalar_field("name")])
        # 'rare' in only 1 of 10 notes (10%) < default 25% threshold
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(10)
        ]
        notes[0] = _note("n0", observations=[("name", "P0"), ("rare", "x")])
        drift = diff_schema(schema, notes)
        new_names = [f.name for f in drift.new_fields]
        assert "rare" not in new_names

    def test_new_relation_detected(self):
        """Relation common in notes but not in schema -> new field."""
        schema = _make_schema([_scalar_field("name")])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")],
                  relations=[("works_at", f"Org{i}")])
            for i in range(4)
        ]
        drift = diff_schema(schema, notes)
        new_names = [f.name for f in drift.new_fields]
        assert "works_at" in new_names


# --- Dropped fields ---


class TestDiffDroppedFields:
    def test_dropped_field_not_in_any_note(self):
        """Schema field that never appears in notes -> dropped."""
        schema = _make_schema([_scalar_field("name"), _scalar_field("legacy_id")])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(5)
        ]
        drift = diff_schema(schema, notes)

        dropped_names = [f.name for f in drift.dropped_fields]
        assert "legacy_id" in dropped_names

    def test_dropped_field_below_threshold(self):
        """Schema field appearing rarely -> dropped."""
        schema = _make_schema([_scalar_field("name"), _scalar_field("fax")])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(20)
        ]
        notes[0] = _note("n0", observations=[("name", "P0"), ("fax", "555-1234")])
        drift = diff_schema(schema, notes)
        dropped_names = [f.name for f in drift.dropped_fields]
        assert "fax" in dropped_names  # 1/20 = 5% < 10%

    def test_field_above_threshold_not_dropped(self):
        schema = _make_schema([_scalar_field("name")])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(5)
        ]
        drift = diff_schema(schema, notes)
        assert drift.dropped_fields == []

    def test_dropped_entity_ref_field(self):
        """Entity ref field not appearing in relations -> dropped."""
        schema = _make_schema([_scalar_field("name"), _entity_ref_field("works_at")])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(5)
        ]
        drift = diff_schema(schema, notes)
        dropped_names = [f.name for f in drift.dropped_fields]
        assert "works_at" in dropped_names
        works_at_dropped = next(f for f in drift.dropped_fields if f.name == "works_at")
        assert works_at_dropped.source == "relation"


# --- Cardinality changes ---


class TestDiffCardinalityChanges:
    def test_schema_single_usage_array(self):
        """Schema says single-value but usage is typically array."""
        schema = _make_schema([_scalar_field("tag", is_array=False)])
        notes = [
            _note("n0", observations=[("tag", "python"), ("tag", "mcp")]),
            _note("n1", observations=[("tag", "schema"), ("tag", "validation")]),
            _note("n2", observations=[("tag", "ai"), ("tag", "llm")]),
        ]
        drift = diff_schema(schema, notes)
        assert len(drift.cardinality_changes) == 1
        assert "tag" in drift.cardinality_changes[0]
        assert "array" in drift.cardinality_changes[0]

    def test_schema_array_usage_single(self):
        """Schema says array but usage is typically single-value."""
        schema = _make_schema([_scalar_field("name", is_array=True)])
        notes = [
            _note(f"n{i}", observations=[("name", f"Person {i}")])
            for i in range(5)
        ]
        drift = diff_schema(schema, notes)
        assert len(drift.cardinality_changes) == 1
        assert "name" in drift.cardinality_changes[0]
        assert "single-value" in drift.cardinality_changes[0]

    def test_no_cardinality_change_when_matching(self):
        schema = _make_schema([_scalar_field("name", is_array=False)])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(5)
        ]
        drift = diff_schema(schema, notes)
        assert drift.cardinality_changes == []

    def test_cardinality_not_reported_for_absent_field(self):
        """If a schema field doesn't appear in notes, no cardinality check."""
        schema = _make_schema([_scalar_field("ghost", is_array=True)])
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(5)
        ]
        drift = diff_schema(schema, notes)
        assert drift.cardinality_changes == []
        dropped_names = [f.name for f in drift.dropped_fields]
        assert "ghost" in dropped_names
