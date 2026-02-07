"""Tests for basic_memory.schema.parser -- Picoschema parsing."""

import pytest

from basic_memory.schema.parser import (
    SchemaDefinition,
    parse_picoschema,
    parse_schema_note,
    _parse_field_key,
    _parse_type_and_description,
    _is_entity_ref_type,
    SCALAR_TYPES,
)


# --- _parse_field_key ---


class TestParseFieldKey:
    def test_simple_required(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("name")
        assert name == "name"
        assert required is True
        assert is_array is False
        assert is_enum is False
        assert is_object is False

    def test_optional(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("role?")
        assert name == "role"
        assert required is False

    def test_array(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("tags(array)")
        assert name == "tags"
        assert required is True
        assert is_array is True

    def test_optional_array(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("tags?(array)")
        assert name == "tags"
        assert required is False
        assert is_array is True

    def test_enum(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("status?(enum)")
        assert name == "status"
        assert required is False
        assert is_enum is True

    def test_object(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("metadata?(object)")
        assert name == "metadata"
        assert required is False
        assert is_object is True

    def test_required_enum(self):
        name, required, is_array, is_enum, is_object = _parse_field_key("status(enum)")
        assert name == "status"
        assert required is True
        assert is_enum is True


# --- _parse_type_and_description ---


class TestParseTypeAndDescription:
    def test_type_only(self):
        type_str, desc = _parse_type_and_description("string")
        assert type_str == "string"
        assert desc is None

    def test_type_with_description(self):
        type_str, desc = _parse_type_and_description("string, full name")
        assert type_str == "string"
        assert desc == "full name"

    def test_entity_ref_with_description(self):
        type_str, desc = _parse_type_and_description("Organization, employer")
        assert type_str == "Organization"
        assert desc == "employer"

    def test_whitespace_handling(self):
        type_str, desc = _parse_type_and_description("  string , a description  ")
        assert type_str == "string"
        assert desc == "a description"


# --- _is_entity_ref_type ---


class TestIsEntityRefType:
    def test_scalar_types_not_entity_ref(self):
        for scalar in SCALAR_TYPES:
            assert _is_entity_ref_type(scalar) is False

    def test_capitalized_is_entity_ref(self):
        assert _is_entity_ref_type("Organization") is True
        assert _is_entity_ref_type("Person") is True

    def test_lowercase_not_entity_ref(self):
        assert _is_entity_ref_type("custom") is False

    def test_empty_string(self):
        assert _is_entity_ref_type("") is False


# --- parse_picoschema ---


class TestParsePicoschema:
    def test_required_string_field(self):
        fields = parse_picoschema({"name": "string"})
        assert len(fields) == 1
        assert fields[0].name == "name"
        assert fields[0].type == "string"
        assert fields[0].required is True

    def test_optional_field(self):
        fields = parse_picoschema({"role?": "string"})
        assert fields[0].name == "role"
        assert fields[0].required is False

    def test_field_with_description(self):
        fields = parse_picoschema({"name": "string, full name"})
        assert fields[0].description == "full name"

    def test_array_field(self):
        fields = parse_picoschema({"tags?(array)": "string"})
        assert fields[0].name == "tags"
        assert fields[0].is_array is True
        assert fields[0].required is False

    def test_entity_ref_field(self):
        fields = parse_picoschema({"works_at?": "Organization, employer"})
        assert fields[0].name == "works_at"
        assert fields[0].type == "Organization"
        assert fields[0].is_entity_ref is True
        assert fields[0].description == "employer"

    def test_enum_field_with_list(self):
        fields = parse_picoschema({"status?(enum)": ["active", "inactive"]})
        assert fields[0].name == "status"
        assert fields[0].is_enum is True
        assert fields[0].enum_values == ["active", "inactive"]

    def test_enum_field_with_string(self):
        fields = parse_picoschema({"status?(enum)": "active"})
        assert fields[0].is_enum is True
        assert fields[0].enum_values == ["active"]

    def test_enum_values_coerced_to_string(self):
        fields = parse_picoschema({"year?(enum)": [2020, 2021, 2022]})
        assert fields[0].enum_values == ["2020", "2021", "2022"]

    def test_object_field(self):
        fields = parse_picoschema({
            "address?(object)": {
                "street": "string",
                "city": "string",
            }
        })
        assert fields[0].name == "address"
        assert fields[0].type == "object"
        assert len(fields[0].children) == 2
        assert fields[0].children[0].name == "street"
        assert fields[0].children[1].name == "city"

    def test_dict_value_treated_as_object(self):
        """A dict value without explicit (object) is still treated as an object."""
        fields = parse_picoschema({
            "metadata": {
                "source": "string",
            }
        })
        assert fields[0].type == "object"
        assert len(fields[0].children) == 1

    def test_multiple_fields(self):
        fields = parse_picoschema({
            "name": "string",
            "role?": "string",
            "works_at?": "Organization",
        })
        assert len(fields) == 3
        names = [f.name for f in fields]
        assert "name" in names
        assert "role" in names
        assert "works_at" in names


# --- parse_schema_note ---


class TestParseSchemaNote:
    def test_basic_schema_note(self):
        frontmatter = {
            "type": "schema",
            "entity": "Person",
            "version": 2,
            "schema": {
                "name": "string",
                "role?": "string",
            },
        }
        result = parse_schema_note(frontmatter)
        assert isinstance(result, SchemaDefinition)
        assert result.entity == "Person"
        assert result.version == 2
        assert len(result.fields) == 2
        assert result.validation_mode == "warn"

    def test_default_version(self):
        frontmatter = {
            "entity": "Person",
            "schema": {"name": "string"},
        }
        result = parse_schema_note(frontmatter)
        assert result.version == 1

    def test_strict_validation_mode(self):
        frontmatter = {
            "entity": "Person",
            "schema": {"name": "string"},
            "settings": {"validation": "strict"},
        }
        result = parse_schema_note(frontmatter)
        assert result.validation_mode == "strict"

    def test_missing_entity_raises(self):
        with pytest.raises(ValueError, match="entity"):
            parse_schema_note({"schema": {"name": "string"}})

    def test_missing_schema_dict_raises(self):
        with pytest.raises(ValueError, match="schema"):
            parse_schema_note({"entity": "Person"})

    def test_schema_not_dict_raises(self):
        with pytest.raises(ValueError, match="schema"):
            parse_schema_note({"entity": "Person", "schema": "not-a-dict"})

    def test_non_dict_settings_defaults_to_warn(self):
        frontmatter = {
            "entity": "Person",
            "schema": {"name": "string"},
            "settings": "invalid",
        }
        result = parse_schema_note(frontmatter)
        assert result.validation_mode == "warn"
