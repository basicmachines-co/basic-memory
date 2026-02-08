"""Integration tests for the Picoschema parser using real fixture files."""

import pytest

from basic_memory.schema.parser import parse_schema_note


class TestPersonSchemaParsing:
    """Parse the Person.md schema fixture and verify all fields."""

    def test_person_schema_entity(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        assert schema.entity == "Person"

    def test_person_schema_version(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        assert schema.version == 1

    def test_person_schema_validation_mode(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        assert schema.validation_mode == "warn"

    def test_person_schema_field_count(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        assert len(schema.fields) == 5

    def test_person_name_field_is_required(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        name_field = next(f for f in schema.fields if f.name == "name")
        assert name_field.required is True
        assert name_field.type == "string"
        assert name_field.description == "full name"

    def test_person_role_field_is_optional(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        role_field = next(f for f in schema.fields if f.name == "role")
        assert role_field.required is False

    def test_person_works_at_is_entity_ref(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        works_at = next(f for f in schema.fields if f.name == "works_at")
        assert works_at.is_entity_ref is True
        assert works_at.type == "Organization"

    def test_person_expertise_is_array(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        expertise = next(f for f in schema.fields if f.name == "expertise")
        assert expertise.is_array is True

    def test_person_email_is_optional(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        email = next(f for f in schema.fields if f.name == "email")
        assert email.required is False


class TestBookSchemaParsing:
    """Parse the Book.md schema fixture."""

    def test_book_genre_is_enum(self, book_schema_frontmatter):
        schema = parse_schema_note(book_schema_frontmatter)
        genre = next(f for f in schema.fields if f.name == "genre")
        assert genre.is_enum is True
        assert set(genre.enum_values) == {"fiction", "nonfiction", "technical"}


class TestStrictSchemaParsing:
    """Verify strict validation mode is parsed."""

    def test_strict_validation_mode(self, strict_schema_frontmatter):
        schema = parse_schema_note(strict_schema_frontmatter)
        assert schema.validation_mode == "strict"


class TestSchemaParsingErrors:
    """Verify parser raises on invalid frontmatter."""

    def test_missing_entity_raises(self):
        with pytest.raises(ValueError, match="entity"):
            parse_schema_note({"type": "schema", "schema": {"name": "string"}})

    def test_missing_schema_dict_raises(self):
        with pytest.raises(ValueError, match="schema"):
            parse_schema_note({"type": "schema", "entity": "Person"})
