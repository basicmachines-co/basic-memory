"""Tests for basic_memory.schema.inference -- schema inference from usage patterns."""

from basic_memory.schema.inference import (
    NoteData,
    ObservationData,
    RelationData,
    InferenceResult,
    infer_schema,
)

# Short aliases for test readability
Obs = ObservationData
Rel = RelationData


# --- Helpers ---


def _note(
    identifier: str,
    observations: list[ObservationData] | None = None,
    relations: list[RelationData] | None = None,
) -> NoteData:
    return NoteData(
        identifier=identifier,
        observations=observations or [],
        relations=relations or [],
    )


# --- Empty notes ---


class TestInferEmpty:
    def test_empty_notes_list(self):
        result = infer_schema("Person", [])
        assert isinstance(result, InferenceResult)
        assert result.notes_analyzed == 0
        assert result.field_frequencies == []
        assert result.suggested_schema == {}
        assert result.suggested_required == []
        assert result.suggested_optional == []
        assert result.excluded == []


# --- Single note ---


class TestInferSingleNote:
    def test_single_note_all_fields_at_100_percent(self):
        note = _note("n1", observations=[Obs("name", "Alice"), Obs("role", "Engineer")])
        result = infer_schema("Person", [note])

        assert result.notes_analyzed == 1
        assert len(result.suggested_required) == 2
        assert "name" in result.suggested_required
        assert "role" in result.suggested_required

    def test_single_note_with_relations(self):
        note = _note(
            "n1",
            observations=[Obs("name", "Alice")],
            relations=[Rel("works_at", "Acme")],
        )
        result = infer_schema("Person", [note])
        assert len(result.suggested_required) == 2
        assert "works_at" in result.suggested_required


# --- Frequency thresholds ---


class TestInferThresholds:
    def test_95_percent_required(self):
        """Fields at 95%+ become required."""
        notes = []
        for i in range(20):
            obs = [Obs("name", f"Person {i}")]
            if i < 19:
                obs.append(Obs("bio", f"Bio {i}"))
            notes.append(_note(f"n{i}", observations=obs))

        result = infer_schema("Person", notes)
        assert "name" in result.suggested_required
        assert "bio" in result.suggested_required  # 19/20 = 95%

    def test_below_95_percent_optional(self):
        """Fields between 25% and 95% become optional."""
        notes = []
        for i in range(10):
            obs = [Obs("name", f"Person {i}")]
            if i < 5:
                obs.append(Obs("role", f"Role {i}"))
            notes.append(_note(f"n{i}", observations=obs))

        result = infer_schema("Person", notes)
        assert "name" in result.suggested_required
        assert "role" in result.suggested_optional  # 50%

    def test_below_25_percent_excluded(self):
        """Fields below 25% are excluded."""
        notes = []
        for i in range(10):
            obs = [Obs("name", f"Person {i}")]
            if i < 2:
                obs.append(Obs("rare", f"Rare {i}"))
            notes.append(_note(f"n{i}", observations=obs))

        result = infer_schema("Person", notes)
        assert "rare" in result.excluded  # 20%
        assert "rare" not in result.suggested_required
        assert "rare" not in result.suggested_optional

    def test_custom_thresholds(self):
        """Custom thresholds override defaults."""
        notes = [_note(f"n{i}", observations=[Obs("field", f"val{i}")]) for i in range(3)]
        notes.append(_note("n3"))

        result = infer_schema(
            "Test",
            notes,
            required_threshold=0.80,
            optional_threshold=0.50,
        )
        assert "field" in result.suggested_optional  # 75% < 80%
        assert "field" not in result.suggested_required


# --- Array detection ---


class TestInferArrayDetection:
    def test_array_detected_when_multiple_per_note(self):
        """Category appearing multiple times in >50% of containing notes -> array."""
        notes = [
            _note("n0", observations=[Obs("tag", "python"), Obs("tag", "mcp")]),
            _note("n1", observations=[Obs("tag", "schema"), Obs("tag", "validation")]),
            _note("n2", observations=[Obs("tag", "ai"), Obs("tag", "llm")]),
            _note("n3", observations=[Obs("tag", "single")]),
        ]
        result = infer_schema("Project", notes)

        tag_freq = next(f for f in result.field_frequencies if f.name == "tag")
        assert tag_freq.is_array is True

    def test_single_value_not_array(self):
        notes = [_note(f"n{i}", observations=[Obs("name", f"Person {i}")]) for i in range(5)]
        result = infer_schema("Person", notes)
        name_freq = next(f for f in result.field_frequencies if f.name == "name")
        assert name_freq.is_array is False


# --- Relation frequency ---


class TestInferRelations:
    def test_relation_frequency(self):
        notes = [_note(f"n{i}", relations=[Rel("works_at", f"Org{i}")]) for i in range(3)]
        notes.append(_note("n3"))
        result = infer_schema("Person", notes)

        works_at = next(f for f in result.field_frequencies if f.name == "works_at")
        assert works_at.source == "relation"
        assert works_at.percentage == 0.75
        assert "works_at" in result.suggested_optional

    def test_relation_array_detection(self):
        notes = [
            _note("n0", relations=[Rel("knows", "Alice"), Rel("knows", "Bob")]),
            _note("n1", relations=[Rel("knows", "Charlie"), Rel("knows", "Dave")]),
            _note("n2", relations=[Rel("knows", "Eve")]),
        ]
        result = infer_schema("Person", notes)
        knows_freq = next(f for f in result.field_frequencies if f.name == "knows")
        assert knows_freq.is_array is True


# --- Sample values ---


class TestInferSampleValues:
    def test_sample_values_collected(self):
        notes = [_note(f"n{i}", observations=[Obs("name", f"Person {i}")]) for i in range(3)]
        result = infer_schema("Person", notes)
        name_freq = next(f for f in result.field_frequencies if f.name == "name")
        assert len(name_freq.sample_values) == 3

    def test_sample_values_capped_at_max(self):
        notes = [_note(f"n{i}", observations=[Obs("name", f"Person {i}")]) for i in range(10)]
        result = infer_schema("Person", notes, max_sample_values=5)
        name_freq = next(f for f in result.field_frequencies if f.name == "name")
        assert len(name_freq.sample_values) == 5


# --- Suggested schema dict ---


class TestInferSuggestedSchema:
    def test_required_field_key_format(self):
        """Required fields have bare name (no '?')."""
        notes = [_note("n1", observations=[Obs("name", "Alice")])]
        result = infer_schema("Person", notes)
        assert "name" in result.suggested_schema

    def test_optional_field_key_format(self):
        """Optional fields have '?' suffix."""
        notes = [
            _note("n0", observations=[Obs("name", "A"), Obs("role", "Eng")]),
            _note("n1", observations=[Obs("name", "B"), Obs("role", "PM")]),
            _note("n2", observations=[Obs("name", "C")]),
            _note("n3", observations=[Obs("name", "D")]),
        ]
        result = infer_schema("Person", notes)
        assert "role?" in result.suggested_schema

    def test_array_field_key_format(self):
        """Array fields have '(array)' suffix."""
        notes = [
            _note("n0", observations=[Obs("tag", "a"), Obs("tag", "b")]),
            _note("n1", observations=[Obs("tag", "c"), Obs("tag", "d")]),
        ]
        result = infer_schema("Project", notes)
        assert "tag(array)" in result.suggested_schema

    def test_excluded_fields_not_in_schema(self):
        """Fields below threshold not in suggested schema."""
        notes = [_note(f"n{i}", observations=[Obs("name", f"P{i}")]) for i in range(10)]
        notes[0] = _note("n0", observations=[Obs("name", "P0"), Obs("rare", "x")])
        result = infer_schema("Person", notes)
        for key in result.suggested_schema:
            assert "rare" not in key


# --- Target entity type on relations (bug fix) ---


class TestInferRelationTargetType:
    """Verify that relation target type comes from the individual relation,
    not from the source note's note_type."""

    def test_target_type_from_relation_data(self):
        """Relations with target_note_type produce correct schema suggestions."""
        notes = [
            _note(
                f"n{i}",
                relations=[Rel("works_at", f"Org{i}", target_note_type="organization")],
            )
            for i in range(3)
        ]
        result = infer_schema("Person", notes)
        works_at = next(f for f in result.field_frequencies if f.name == "works_at")
        assert works_at.target_type == "organization"

    def test_target_type_none_when_unresolved(self):
        """Relations without target_note_type produce None target_type."""
        notes = [_note(f"n{i}", relations=[Rel("knows", f"P{i}")]) for i in range(3)]
        result = infer_schema("Person", notes)
        knows = next(f for f in result.field_frequencies if f.name == "knows")
        assert knows.target_type is None

    def test_mixed_target_types_uses_most_common(self):
        """When relations point to different types, the most common wins."""
        notes = [
            _note("n0", relations=[Rel("works_at", "OrgA", target_note_type="organization")]),
            _note("n1", relations=[Rel("works_at", "OrgB", target_note_type="organization")]),
            _note("n2", relations=[Rel("works_at", "SchoolC", target_note_type="school")]),
        ]
        result = infer_schema("Person", notes)
        works_at = next(f for f in result.field_frequencies if f.name == "works_at")
        assert works_at.target_type == "organization"
