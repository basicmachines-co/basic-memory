"""Schema fields are satisfied by a frontmatter key or an observation category (#1973).

Frontmatter is checked first. Inference and drift count frontmatter keys as fields.
"""

from typing import Any

from basic_memory.picoschema.diff import diff_schema
from basic_memory.picoschema.inference import (
    NoteData,
    ObservationData,
    frontmatter_values,
    infer_schema,
)
from basic_memory.picoschema.parser import SchemaDefinition, SchemaField
from basic_memory.picoschema.validator import validate_note

Obs = ObservationData


def _schema(*fields: SchemaField) -> SchemaDefinition:
    return SchemaDefinition(
        entity="meeting", version=1, fields=list(fields), validation_mode="warn"
    )


def _note(
    identifier: str,
    frontmatter: dict[str, Any] | None = None,
    observations: list[ObservationData] | None = None,
) -> NoteData:
    return NoteData(
        identifier=identifier,
        observations=observations or [],
        relations=[],
        frontmatter={"title": identifier, "type": "meeting", **(frontmatter or {})},
    )


ATTENDEES = SchemaField(name="attendees", type="integer", required=True)
STATUS = SchemaField(
    name="status", type="enum", required=True, is_enum=True, enum_values=["open", "done"]
)


# --- Validation ---


def test_a_frontmatter_key_satisfies_a_required_field():
    result = validate_note("standup", _schema(ATTENDEES), [], [], frontmatter={"attendees": 4})

    assert result.warnings == []
    [field_result] = result.field_results
    assert (field_result.status, field_result.values) == ("present", ["4"])


def test_an_observation_still_satisfies_a_field():
    result = validate_note("standup", _schema(ATTENDEES), [Obs("attendees", "4")], [], {})

    assert result.warnings == []
    assert result.field_results[0].values == ["4"]


def test_frontmatter_is_checked_before_observations():
    result = validate_note(
        "standup",
        _schema(STATUS),
        [Obs("status", "blocked")],
        [],
        frontmatter={"status": "open"},
    )

    assert result.warnings == []
    assert result.field_results[0].values == ["open"]
    # The observation of the same name is the field's, not an unmatched category.
    assert result.unmatched_observations == {}


def test_enum_checks_apply_to_frontmatter_values():
    result = validate_note("standup", _schema(STATUS), [], [], frontmatter={"status": "blocked"})

    assert result.field_results[0].status == "enum_mismatch"
    assert result.warnings == ["Field 'status' has invalid value(s): blocked (allowed: open, done)"]


def test_a_frontmatter_list_gives_one_value_per_item():
    topics = SchemaField(name="topics", type="string", required=True, is_array=True)

    result = validate_note("standup", _schema(topics), [], [], {"topics": ["infra", "hiring"]})

    assert result.field_results[0].values == ["infra", "hiring"]


def test_an_empty_frontmatter_key_falls_back_to_observations():
    result = validate_note(
        "standup", _schema(ATTENDEES), [Obs("attendees", "4")], [], {"attendees": []}
    )

    assert result.field_results[0].values == ["4"]


def test_a_missing_field_names_both_places_it_can_live():
    result = validate_note("standup", _schema(ATTENDEES), [], [], frontmatter={})

    assert result.warnings == [
        "Missing required field: attendees "
        "(expected attendees: in frontmatter or a [attendees] observation)"
    ]


def test_frontmatter_values_skip_nulls():
    assert frontmatter_values({"a": None, "b": [None, 2], "c": [], "d": "x"}) == {
        "b": ["2"],
        "d": ["x"],
    }


# --- Inference ---


def test_infer_counts_frontmatter_keys_and_skips_note_keys():
    notes = [
        _note("monday", {"attendees": 4, "tags": ["standup"]}, [Obs("action", "ship")]),
        _note("tuesday", {"attendees": 5}, [Obs("action", "review")]),
    ]

    result = infer_schema("meeting", notes)

    by_name = {freq.name: freq for freq in result.field_frequencies}
    assert set(by_name) == {"attendees", "action"}
    assert by_name["attendees"].source == "frontmatter"
    assert by_name["attendees"].sample_values == ["4", "5"]
    assert result.suggested_schema == {"attendees": "string", "action": "string"}


def test_infer_counts_a_field_once_per_note_from_either_place():
    notes = [
        _note("monday", {"attendees": 4}, [Obs("attendees", "four")]),
        _note("tuesday", observations=[Obs("attendees", "5")]),
        _note("wednesday", observations=[Obs("attendees", "6")]),
    ]

    [freq] = infer_schema("meeting", notes).field_frequencies

    assert (freq.count, freq.is_array) == (3, False)
    # Most of its notes keep it as an observation.
    assert freq.source == "observation"
    assert freq.sample_values == ["4", "5", "6"]


# --- Drift ---


def test_diff_does_not_drop_a_field_kept_in_frontmatter():
    notes = [_note("monday", {"attendees": 4}), _note("tuesday", {"attendees": 5})]

    drift = diff_schema(_schema(ATTENDEES), notes)

    assert drift.dropped_fields == []
    assert drift.new_fields == []


def test_diff_reports_a_new_frontmatter_key():
    notes = [_note("monday", {"attendees": 4, "room": "A"}), _note("tuesday", {"attendees": 5})]

    drift = diff_schema(_schema(ATTENDEES), notes)

    assert [(freq.name, freq.source) for freq in drift.new_fields] == [("room", "frontmatter")]
