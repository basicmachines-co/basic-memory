"""Integration tests for schema inference using the 30+ person fixture corpus."""

import pytest

from basic_memory.schema.inference import infer_schema, NoteData

from test_schema.helpers import parse_observations, parse_relations, INFERENCE_DIR


def load_inference_notes() -> list[NoteData]:
    """Load all person fixture files as NoteData objects."""
    return [
        NoteData(
            identifier=filepath.stem,
            observations=parse_observations(filepath),
            relations=parse_relations(filepath),
        )
        for filepath in sorted(INFERENCE_DIR.glob("*.md"))
    ]


@pytest.fixture
def person_notes() -> list[NoteData]:
    return load_inference_notes()


@pytest.fixture
def inference_result(person_notes):
    return infer_schema("Person", person_notes)


class TestInferenceCorpusSize:
    def test_minimum_notes_loaded(self, person_notes):
        assert len(person_notes) >= 30

    def test_notes_analyzed_matches(self, inference_result, person_notes):
        assert inference_result.notes_analyzed == len(person_notes)


class TestNameFieldInference:
    """Name should be ~100% -> required."""

    def test_name_is_required(self, inference_result):
        assert "name" in inference_result.suggested_required

    def test_name_frequency_near_100(self, inference_result):
        name_freq = next(f for f in inference_result.field_frequencies if f.name == "name")
        assert name_freq.percentage >= 0.95


class TestRoleFieldInference:
    """Role should be ~90% -> optional."""

    def test_role_is_optional(self, inference_result):
        assert "role" in inference_result.suggested_optional

    def test_role_frequency_around_90(self, inference_result):
        role_freq = next(f for f in inference_result.field_frequencies if f.name == "role")
        assert 0.80 <= role_freq.percentage < 0.95


class TestExpertiseFieldInference:
    """Expertise should be ~60% -> optional, array."""

    def test_expertise_is_optional(self, inference_result):
        assert "expertise" in inference_result.suggested_optional

    def test_expertise_is_array(self, inference_result):
        freq = next(f for f in inference_result.field_frequencies if f.name == "expertise")
        assert freq.is_array is True


class TestEmailFieldInference:
    """Email should be ~27% -> optional."""

    def test_email_is_optional(self, inference_result):
        assert "email" in inference_result.suggested_optional


class TestBornFieldInference:
    """Born should be ~17% -> excluded."""

    def test_born_is_excluded(self, inference_result):
        assert "born" in inference_result.excluded

    def test_born_frequency_below_threshold(self, inference_result):
        born_freq = next(f for f in inference_result.field_frequencies if f.name == "born")
        assert born_freq.percentage < 0.25


class TestWorksAtRelationInference:
    """works_at relation should be ~73% -> optional."""

    def test_works_at_is_optional(self, inference_result):
        assert "works_at" in inference_result.suggested_optional

    def test_works_at_is_relation(self, inference_result):
        freq = next(f for f in inference_result.field_frequencies if f.name == "works_at")
        assert freq.source == "relation"


class TestSuggestedSchema:
    def test_suggested_schema_not_empty(self, inference_result):
        assert len(inference_result.suggested_schema) > 0

    def test_excluded_fields_not_in_schema(self, inference_result):
        schema_names = set()
        for key in inference_result.suggested_schema:
            name = key.replace("?", "").split("(")[0]
            schema_names.add(name)
        for excluded in inference_result.excluded:
            assert excluded not in schema_names
