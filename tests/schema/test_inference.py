"""Tests for basic_memory.schema.inference -- schema inference from usage patterns."""

from basic_memory.schema.inference import (
    NoteData,
    InferenceResult,
    infer_schema,
)


# --- Helpers ---


def _note(
    identifier: str,
    observations: list[tuple[str, str]] | None = None,
    relations: list[tuple[str, str]] | None = None,
    entity_type: str | None = None,
) -> NoteData:
    return NoteData(
        identifier=identifier,
        observations=observations or [],
        relations=relations or [],
        entity_type=entity_type,
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
        note = _note("n1", observations=[("name", "Alice"), ("role", "Engineer")])
        result = infer_schema("Person", [note])

        assert result.notes_analyzed == 1
        assert len(result.suggested_required) == 2
        assert "name" in result.suggested_required
        assert "role" in result.suggested_required

    def test_single_note_with_relations(self):
        note = _note(
            "n1",
            observations=[("name", "Alice")],
            relations=[("works_at", "Acme")],
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
            obs = [("name", f"Person {i}")]
            if i < 19:
                obs.append(("bio", f"Bio {i}"))
            notes.append(_note(f"n{i}", observations=obs))

        result = infer_schema("Person", notes)
        assert "name" in result.suggested_required
        assert "bio" in result.suggested_required  # 19/20 = 95%

    def test_below_95_percent_optional(self):
        """Fields between 25% and 95% become optional."""
        notes = []
        for i in range(10):
            obs = [("name", f"Person {i}")]
            if i < 5:
                obs.append(("role", f"Role {i}"))
            notes.append(_note(f"n{i}", observations=obs))

        result = infer_schema("Person", notes)
        assert "name" in result.suggested_required
        assert "role" in result.suggested_optional  # 50%

    def test_below_25_percent_excluded(self):
        """Fields below 25% are excluded."""
        notes = []
        for i in range(10):
            obs = [("name", f"Person {i}")]
            if i < 2:
                obs.append(("rare", f"Rare {i}"))
            notes.append(_note(f"n{i}", observations=obs))

        result = infer_schema("Person", notes)
        assert "rare" in result.excluded  # 20%
        assert "rare" not in result.suggested_required
        assert "rare" not in result.suggested_optional

    def test_custom_thresholds(self):
        """Custom thresholds override defaults."""
        notes = [
            _note(f"n{i}", observations=[("field", f"val{i}")])
            for i in range(3)
        ]
        notes.append(_note("n3"))

        result = infer_schema(
            "Test", notes,
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
            _note("n0", observations=[("tag", "python"), ("tag", "mcp")]),
            _note("n1", observations=[("tag", "schema"), ("tag", "validation")]),
            _note("n2", observations=[("tag", "ai"), ("tag", "llm")]),
            _note("n3", observations=[("tag", "single")]),
        ]
        result = infer_schema("Project", notes)

        tag_freq = next(f for f in result.field_frequencies if f.name == "tag")
        assert tag_freq.is_array is True

    def test_single_value_not_array(self):
        notes = [
            _note(f"n{i}", observations=[("name", f"Person {i}")])
            for i in range(5)
        ]
        result = infer_schema("Person", notes)
        name_freq = next(f for f in result.field_frequencies if f.name == "name")
        assert name_freq.is_array is False


# --- Relation frequency ---


class TestInferRelations:
    def test_relation_frequency(self):
        notes = [
            _note(f"n{i}", relations=[("works_at", f"Org{i}")])
            for i in range(3)
        ]
        notes.append(_note("n3"))
        result = infer_schema("Person", notes)

        works_at = next(f for f in result.field_frequencies if f.name == "works_at")
        assert works_at.source == "relation"
        assert works_at.percentage == 0.75
        assert "works_at" in result.suggested_optional

    def test_relation_array_detection(self):
        notes = [
            _note("n0", relations=[("knows", "Alice"), ("knows", "Bob")]),
            _note("n1", relations=[("knows", "Charlie"), ("knows", "Dave")]),
            _note("n2", relations=[("knows", "Eve")]),
        ]
        result = infer_schema("Person", notes)
        knows_freq = next(f for f in result.field_frequencies if f.name == "knows")
        assert knows_freq.is_array is True


# --- Sample values ---


class TestInferSampleValues:
    def test_sample_values_collected(self):
        notes = [
            _note(f"n{i}", observations=[("name", f"Person {i}")])
            for i in range(3)
        ]
        result = infer_schema("Person", notes)
        name_freq = next(f for f in result.field_frequencies if f.name == "name")
        assert len(name_freq.sample_values) == 3

    def test_sample_values_capped_at_max(self):
        notes = [
            _note(f"n{i}", observations=[("name", f"Person {i}")])
            for i in range(10)
        ]
        result = infer_schema("Person", notes, max_sample_values=5)
        name_freq = next(f for f in result.field_frequencies if f.name == "name")
        assert len(name_freq.sample_values) == 5


# --- Suggested schema dict ---


class TestInferSuggestedSchema:
    def test_required_field_key_format(self):
        """Required fields have bare name (no '?')."""
        notes = [_note("n1", observations=[("name", "Alice")])]
        result = infer_schema("Person", notes)
        assert "name" in result.suggested_schema

    def test_optional_field_key_format(self):
        """Optional fields have '?' suffix."""
        notes = [
            _note("n0", observations=[("name", "A"), ("role", "Eng")]),
            _note("n1", observations=[("name", "B"), ("role", "PM")]),
            _note("n2", observations=[("name", "C")]),
            _note("n3", observations=[("name", "D")]),
        ]
        result = infer_schema("Person", notes)
        assert "role?" in result.suggested_schema

    def test_array_field_key_format(self):
        """Array fields have '(array)' suffix."""
        notes = [
            _note("n0", observations=[("tag", "a"), ("tag", "b")]),
            _note("n1", observations=[("tag", "c"), ("tag", "d")]),
        ]
        result = infer_schema("Project", notes)
        assert "tag(array)" in result.suggested_schema

    def test_excluded_fields_not_in_schema(self):
        """Fields below threshold not in suggested schema."""
        notes = [
            _note(f"n{i}", observations=[("name", f"P{i}")])
            for i in range(10)
        ]
        notes[0] = _note("n0", observations=[("name", "P0"), ("rare", "x")])
        result = infer_schema("Person", notes)
        for key in result.suggested_schema:
            assert "rare" not in key
