"""Integration tests for schema validation using real fixture files."""

import pytest

from basic_memory.schema.parser import parse_schema_note, parse_picoschema, SchemaDefinition
from basic_memory.schema.validator import validate_note
from basic_memory.schema.resolver import resolve_schema

from test_schema.helpers import (
    parse_frontmatter,
    parse_observations,
    parse_relations,
    VALID_DIR,
    WARNINGS_DIR,
    EDGE_CASES_DIR,
    SCHEMAS_DIR,
)


class TestValidNotesPassValidation:
    """Validate notes that should pass against the Person schema."""

    def test_paul_graham_passes(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = VALID_DIR / "paul-graham.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("paul-graham", schema, obs, rels)
        assert result.passed is True
        assert len(result.errors) == 0

    def test_paul_graham_name_present(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = VALID_DIR / "paul-graham.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("paul-graham", schema, obs, rels)
        name_result = next(r for r in result.field_results if r.field.name == "name")
        assert name_result.status == "present"
        assert "Paul Graham" in name_result.values

    def test_paul_graham_unmatched_observations(self, person_schema_frontmatter):
        """Extra observations should appear as unmatched, not warnings."""
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = VALID_DIR / "paul-graham.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("paul-graham", schema, obs, rels)
        assert "fact" in result.unmatched_observations

    def test_rich_hickey_passes(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = VALID_DIR / "rich-hickey.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("rich-hickey", schema, obs, rels)
        assert result.passed is True


class TestWarningNotes:
    """Notes that should produce warnings but still pass."""

    def test_missing_required_name_warns(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = WARNINGS_DIR / "missing-required.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("missing-required", schema, obs, rels)
        assert result.passed is True
        name_result = next(r for r in result.field_results if r.field.name == "name")
        assert name_result.status == "missing"
        assert any("name" in w for w in result.warnings)

    def test_wrong_enum_value_warns(self, book_schema_frontmatter):
        schema = parse_schema_note(book_schema_frontmatter)
        filepath = WARNINGS_DIR / "wrong-enum-value.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("wrong-enum", schema, obs, rels)
        genre_result = next(r for r in result.field_results if r.field.name == "genre")
        assert genre_result.status == "enum_mismatch"


class TestInlineSchemaValidation:
    """Validate notes with inline schemas."""

    def test_standup_with_inline_schema(self):
        filepath = VALID_DIR / "standup-2024-01-15.md"
        frontmatter = parse_frontmatter(filepath)
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        assert isinstance(frontmatter.get("schema"), dict)
        fields = parse_picoschema(frontmatter["schema"])
        schema = SchemaDefinition(
            entity=frontmatter.get("type", "unknown"),
            version=1,
            fields=fields,
            validation_mode="warn",
        )
        result = validate_note("standup", schema, obs, rels)
        assert result.passed is True


class TestEdgeCaseValidation:
    """Validation edge cases."""

    def test_empty_note_missing_all_fields(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = EDGE_CASES_DIR / "empty-note.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("empty-note", schema, obs, rels)
        assert all(r.status == "missing" for r in result.field_results)

    def test_unicode_fields_validate(self, person_schema_frontmatter):
        schema = parse_schema_note(person_schema_frontmatter)
        filepath = EDGE_CASES_DIR / "unicode-fields.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("unicode", schema, obs, rels)
        name_result = next(r for r in result.field_results if r.field.name == "name")
        assert name_result.status == "present"

    def test_array_single_value_passes(self, meeting_schema_frontmatter):
        schema = parse_schema_note(meeting_schema_frontmatter)
        filepath = EDGE_CASES_DIR / "array-single.md"
        obs = parse_observations(filepath)
        rels = parse_relations(filepath)
        result = validate_note("array-single", schema, obs, rels)
        attendees = next(r for r in result.field_results if r.field.name == "attendees")
        assert attendees.status == "present"
        assert len(attendees.values) == 1


class TestResolverIntegration:
    """Test schema resolution priority with fixture files."""

    @pytest.mark.asyncio
    async def test_inline_schema_overrides_type(self):
        filepath = EDGE_CASES_DIR / "inline-and-type.md"
        frontmatter = parse_frontmatter(filepath)

        async def mock_search(query: str) -> list[dict]:
            if query == "Person":
                return [parse_frontmatter(SCHEMAS_DIR / "Person.md")]
            return []

        schema = await resolve_schema(frontmatter, mock_search)
        assert schema is not None
        field_names = {f.name for f in schema.fields}
        assert "topic" in field_names
        assert "name" not in field_names

    @pytest.mark.asyncio
    async def test_explicit_ref_overrides_type(self):
        filepath = EDGE_CASES_DIR / "explicit-overrides-type.md"
        frontmatter = parse_frontmatter(filepath)

        async def mock_search(query: str) -> list[dict]:
            if query == "Meeting":
                return [parse_frontmatter(SCHEMAS_DIR / "Meeting.md")]
            if query == "Person":
                return [parse_frontmatter(SCHEMAS_DIR / "Person.md")]
            return []

        schema = await resolve_schema(frontmatter, mock_search)
        assert schema is not None
        assert schema.entity == "Meeting"

    @pytest.mark.asyncio
    async def test_no_schema_returns_none(self):
        filepath = VALID_DIR / "no-schema-note.md"
        frontmatter = parse_frontmatter(filepath)

        async def mock_search(query: str) -> list[dict]:
            return []

        schema = await resolve_schema(frontmatter, mock_search)
        assert schema is None
