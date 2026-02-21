"""Tests for basic_memory.schema.validator -- note validation against schemas."""

from basic_memory.schema.inference import ObservationData, RelationData
from basic_memory.schema.parser import SchemaField, SchemaDefinition
from basic_memory.schema.validator import validate_note

# Short aliases for test readability
Obs = ObservationData
Rel = RelationData


# --- Test Helpers ---


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
        name=name,
        type="Organization",
        required=required,
        is_entity_ref=True,
        is_array=is_array,
    )


def _enum_field(
    name: str,
    values: list[str],
    required: bool = True,
) -> SchemaField:
    return SchemaField(
        name=name,
        type="enum",
        required=required,
        is_enum=True,
        enum_values=values,
    )


def _make_schema(
    fields: list[SchemaField],
    validation_mode: str = "warn",
    entity: str = "TestEntity",
) -> SchemaDefinition:
    return SchemaDefinition(
        entity=entity,
        version=1,
        fields=fields,
        validation_mode=validation_mode,
    )


# --- Required field present/missing ---


class TestValidateRequiredFields:
    def test_required_field_present(self):
        schema = _make_schema([_scalar_field("name")])
        result = validate_note("test-note", schema, [Obs("name", "Alice")], [])

        assert result.passed is True
        assert result.field_results[0].status == "present"
        assert result.field_results[0].values == ["Alice"]
        assert result.warnings == []
        assert result.errors == []

    def test_required_field_missing_warn_mode(self):
        schema = _make_schema([_scalar_field("name")])
        result = validate_note("test-note", schema, [], [])

        assert result.passed is True  # warn mode doesn't fail
        assert result.field_results[0].status == "missing"
        assert len(result.warnings) == 1
        assert "name" in result.warnings[0]

    def test_required_field_missing_strict_mode(self):
        schema = _make_schema([_scalar_field("name")], validation_mode="strict")
        result = validate_note("test-note", schema, [], [])

        assert result.passed is False
        assert len(result.errors) == 1
        assert "name" in result.errors[0]


# --- Optional field behavior ---


class TestValidateOptionalFields:
    def test_optional_field_present(self):
        schema = _make_schema([_scalar_field("bio", required=False)])
        result = validate_note("test-note", schema, [Obs("bio", "A bio")], [])

        assert result.passed is True
        assert result.field_results[0].status == "present"

    def test_optional_field_missing_is_silent(self):
        """Missing optional fields should NOT generate warnings or errors."""
        schema = _make_schema([_scalar_field("bio", required=False)])
        result = validate_note("test-note", schema, [], [])

        assert result.passed is True
        assert result.field_results[0].status == "missing"
        # This is the key behavior: optional missing = silent
        assert result.warnings == []
        assert result.errors == []

    def test_optional_missing_silent_even_in_strict(self):
        """Strict mode only affects required fields."""
        schema = _make_schema(
            [_scalar_field("bio", required=False)],
            validation_mode="strict",
        )
        result = validate_note("test-note", schema, [], [])

        assert result.passed is True
        assert result.errors == []
        assert result.warnings == []


# --- Entity ref fields map to relations ---


class TestValidateEntityRefField:
    def test_entity_ref_checks_relations(self):
        schema = _make_schema([_entity_ref_field("works_at")])
        result = validate_note("test-note", schema, [], [Rel("works_at", "Acme Corp")])

        assert result.passed is True
        assert result.field_results[0].status == "present"
        assert result.field_results[0].values == ["Acme Corp"]

    def test_entity_ref_missing_required_warns(self):
        schema = _make_schema([_entity_ref_field("works_at")])
        result = validate_note("test-note", schema, [], [])

        assert result.passed is True
        assert len(result.warnings) == 1
        assert "works_at" in result.warnings[0]

    def test_entity_ref_missing_optional_silent(self):
        schema = _make_schema([_entity_ref_field("works_at", required=False)])
        result = validate_note("test-note", schema, [], [])

        assert result.passed is True
        assert result.warnings == []

    def test_entity_ref_missing_required_strict_fails(self):
        schema = _make_schema(
            [_entity_ref_field("works_at")],
            validation_mode="strict",
        )
        result = validate_note("test-note", schema, [], [])
        assert result.passed is False
        assert len(result.errors) == 1


# --- Enum field validation ---


class TestValidateEnumField:
    def test_enum_valid_value(self):
        schema = _make_schema([_enum_field("status", ["active", "inactive"])])
        result = validate_note("test-note", schema, [Obs("status", "active")], [])
        assert result.passed is True
        assert result.field_results[0].status == "present"

    def test_enum_invalid_value_warn_mode(self):
        schema = _make_schema([_enum_field("status", ["active", "inactive"])])
        result = validate_note("test-note", schema, [Obs("status", "archived")], [])

        assert result.passed is True  # warn mode
        fr = result.field_results[0]
        assert fr.status == "enum_mismatch"
        assert "archived" in fr.message
        assert len(result.warnings) == 1

    def test_enum_invalid_value_strict_mode(self):
        schema = _make_schema(
            [_enum_field("status", ["active", "inactive"])],
            validation_mode="strict",
        )
        result = validate_note("test-note", schema, [Obs("status", "archived")], [])
        assert result.passed is False
        assert len(result.errors) == 1

    def test_enum_missing_required(self):
        schema = _make_schema([_enum_field("status", ["active", "inactive"])])
        result = validate_note("test-note", schema, [], [])
        assert result.field_results[0].status == "missing"
        assert len(result.warnings) == 1


# --- Array field ---


class TestValidateArrayField:
    def test_array_collects_all_values(self):
        schema = _make_schema([_scalar_field("tag", is_array=True)])
        observations = [Obs("tag", "python"), Obs("tag", "mcp"), Obs("tag", "schema")]
        result = validate_note("test-note", schema, observations, [])

        assert result.passed is True
        fr = result.field_results[0]
        assert fr.status == "present"
        assert fr.values == ["python", "mcp", "schema"]


# --- Unmatched observations and relations ---


class TestValidateUnmatched:
    def test_unmatched_observations_reported(self):
        schema = _make_schema([_scalar_field("name")])
        observations = [Obs("name", "Alice"), Obs("hobby", "reading"), Obs("hobby", "coding")]
        result = validate_note("test-note", schema, observations, [])

        assert result.passed is True
        assert "hobby" in result.unmatched_observations
        assert result.unmatched_observations["hobby"] == 2

    def test_unmatched_relations_reported(self):
        schema = _make_schema([_entity_ref_field("works_at")])
        relations = [Rel("works_at", "Acme Corp"), Rel("friends_with", "Bob")]
        result = validate_note("test-note", schema, [], relations)

        assert "friends_with" in result.unmatched_relations

    def test_all_unmatched_when_empty_schema(self):
        schema = _make_schema([])
        result = validate_note("test-note", schema, [Obs("extra", "value")], [])
        assert "extra" in result.unmatched_observations


# --- Result metadata ---


# --- Frontmatter field validation ---


class TestValidateFrontmatterFields:
    def _make_fm_schema(
        self,
        frontmatter_fields: list[SchemaField],
        validation_mode: str = "warn",
    ) -> SchemaDefinition:
        return SchemaDefinition(
            entity="TestEntity",
            version=1,
            fields=[],
            validation_mode=validation_mode,
            frontmatter_fields=frontmatter_fields,
        )

    def test_required_frontmatter_key_present(self):
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter={"status": "draft"})
        assert result.passed is True
        assert result.field_results[0].status == "present"
        assert result.field_results[0].values == ["draft"]
        assert result.warnings == []

    def test_required_frontmatter_key_missing_warn(self):
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter={"tags": ["a"]})
        assert result.passed is True
        assert result.field_results[0].status == "missing"
        assert len(result.warnings) == 1
        assert "status" in result.warnings[0]

    def test_required_frontmatter_key_missing_strict(self):
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field], validation_mode="strict")
        result = validate_note("test-note", schema, [], [], frontmatter={"tags": ["a"]})
        assert result.passed is False
        assert len(result.errors) == 1
        assert "status" in result.errors[0]

    def test_optional_frontmatter_key_missing_silent(self):
        fm_field = SchemaField(name="status", type="string", required=False)
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter={"other": "val"})
        assert result.passed is True
        assert result.field_results[0].status == "missing"
        assert result.warnings == []
        assert result.errors == []

    def test_enum_frontmatter_valid_value(self):
        fm_field = SchemaField(
            name="status",
            type="enum",
            required=True,
            is_enum=True,
            enum_values=["draft", "published"],
        )
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter={"status": "draft"})
        assert result.passed is True
        assert result.field_results[0].status == "present"
        assert result.field_results[0].values == ["draft"]

    def test_enum_frontmatter_invalid_value_warn(self):
        fm_field = SchemaField(
            name="status",
            type="enum",
            required=True,
            is_enum=True,
            enum_values=["draft", "published"],
        )
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter={"status": "archived"})
        assert result.passed is True
        assert result.field_results[0].status == "enum_mismatch"
        assert "archived" in result.field_results[0].message
        assert len(result.warnings) == 1

    def test_enum_frontmatter_invalid_value_strict(self):
        fm_field = SchemaField(
            name="status",
            type="enum",
            required=True,
            is_enum=True,
            enum_values=["draft", "published"],
        )
        schema = self._make_fm_schema([fm_field], validation_mode="strict")
        result = validate_note("test-note", schema, [], [], frontmatter={"status": "archived"})
        assert result.passed is False
        assert len(result.errors) == 1

    def test_array_frontmatter_field(self):
        fm_field = SchemaField(name="tags", type="string", required=False, is_array=True)
        schema = self._make_fm_schema([fm_field])
        result = validate_note(
            "test-note",
            schema,
            [],
            [],
            frontmatter={"tags": ["python", "ai"]},
        )
        assert result.passed is True
        assert result.field_results[0].status == "present"
        assert result.field_results[0].values == ["python", "ai"]

    def test_frontmatter_none_skips_validation(self):
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter=None)
        assert result.passed is True
        assert result.field_results == []

    def test_empty_frontmatter_dict_validates_missing_keys(self):
        """frontmatter={} is a valid input — required keys should be flagged missing."""
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field])
        result = validate_note("test-note", schema, [], [], frontmatter={})
        assert result.field_results[0].status == "missing"
        assert len(result.warnings) == 1
        assert "status" in result.warnings[0]

    def test_empty_frontmatter_dict_strict_fails(self):
        """frontmatter={} in strict mode should produce errors for required keys."""
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field], validation_mode="strict")
        result = validate_note("test-note", schema, [], [], frontmatter={})
        assert result.passed is False
        assert len(result.errors) == 1

    def test_empty_frontmatter_fields_skips_validation(self):
        schema = self._make_fm_schema([])
        result = validate_note("test-note", schema, [], [], frontmatter={"status": "draft"})
        assert result.passed is True
        assert result.field_results == []

    def test_extra_frontmatter_keys_ignored(self):
        fm_field = SchemaField(name="status", type="string", required=True)
        schema = self._make_fm_schema([fm_field])
        result = validate_note(
            "test-note",
            schema,
            [],
            [],
            frontmatter={"status": "draft", "extra_key": "value", "another": 42},
        )
        assert result.passed is True
        assert len(result.field_results) == 1
        assert result.field_results[0].field.name == "status"


# --- Result metadata ---


class TestValidateResultMetadata:
    def test_note_identifier_in_result(self):
        schema = _make_schema([])
        result = validate_note("my-note", schema, [], [])
        assert result.note_identifier == "my-note"

    def test_schema_entity_in_result(self):
        schema = _make_schema([], entity="Project")
        result = validate_note("test", schema, [], [])
        assert result.schema_entity == "Project"
