"""Integration tests for schema drift detection using drift fixtures."""

import pytest

from basic_memory.schema.parser import parse_schema_note
from basic_memory.schema.diff import diff_schema
from basic_memory.schema.inference import NoteData

from test_schema.helpers import (
    parse_frontmatter,
    parse_observations,
    parse_relations,
    DRIFT_SCHEMA_DIR,
    DRIFT_PEOPLE_DIR,
)


def load_drift_notes() -> list[NoteData]:
    """Load all drift person fixture files as NoteData objects."""
    return [
        NoteData(
            identifier=filepath.stem,
            observations=parse_observations(filepath),
            relations=parse_relations(filepath),
        )
        for filepath in sorted(DRIFT_PEOPLE_DIR.glob("*.md"))
    ]


@pytest.fixture
def drift_schema():
    frontmatter = parse_frontmatter(DRIFT_SCHEMA_DIR / "Person.md")
    return parse_schema_note(frontmatter)


@pytest.fixture
def drift_notes():
    return load_drift_notes()


@pytest.fixture
def drift_result(drift_schema, drift_notes):
    return diff_schema(drift_schema, drift_notes)


class TestDriftCorpusSize:
    def test_minimum_drift_notes(self, drift_notes):
        assert len(drift_notes) >= 20


class TestNewFieldDetection:
    """github field appears in 60% of notes but is not in the schema."""

    def test_github_detected_as_new_field(self, drift_result):
        new_field_names = [f.name for f in drift_result.new_fields]
        assert "github" in new_field_names


class TestDroppedFieldDetection:
    """role field is in schema but only 5% of notes have it."""

    def test_role_detected_as_dropped(self, drift_result):
        dropped_names = [f.name for f in drift_result.dropped_fields]
        assert "role" in dropped_names


class TestCardinalityChanges:
    """works_at changed from single to multiple per note."""

    def test_works_at_cardinality_change_detected(self, drift_result):
        changes = [msg for msg in drift_result.cardinality_changes if "works_at" in msg]
        assert len(changes) >= 1
