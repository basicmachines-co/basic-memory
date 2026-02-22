"""Pydantic response models for the schema system.

These models define the API response format for schema validation,
inference, and drift detection operations. They mirror the dataclass
structures in basic_memory.schema but are Pydantic models suitable
for API serialization.
"""

from pydantic import BaseModel, Field


# --- Validation Response Models ---


class FieldResultResponse(BaseModel):
    """Result of validating a single schema field against a note."""

    field_name: str
    field_type: str
    required: bool
    status: str = Field(description="One of: present, missing, type_mismatch")
    values: list[str] = Field(default_factory=list, description="Matched values from the note")
    message: str | None = None


class NoteValidationResponse(BaseModel):
    """Validation result for a single note against a schema."""

    note_identifier: str
    schema_entity: str
    passed: bool = Field(description="True if no errors (warnings are OK)")
    field_results: list[FieldResultResponse] = Field(default_factory=list)
    unmatched_observations: dict[str, int] = Field(
        default_factory=dict,
        description="Observation categories not covered by schema, with counts",
    )
    unmatched_relations: list[str] = Field(
        default_factory=list,
        description="Relation types not covered by schema",
    )
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Full validation report for one or more notes."""

    note_type: str | None = None
    total_notes: int = 0
    total_entities: int = 0
    valid_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    results: list[NoteValidationResponse] = Field(default_factory=list)


# --- Inference Response Models ---


class FieldFrequencyResponse(BaseModel):
    """Frequency analysis for a single field across notes."""

    name: str
    source: str = Field(description="One of: observation, relation")
    count: int = Field(description="Number of notes containing this field")
    total: int = Field(description="Total notes analyzed")
    percentage: float
    sample_values: list[str] = Field(default_factory=list)
    is_array: bool = Field(
        default=False,
        description="True if field typically appears multiple times per note",
    )
    target_type: str | None = Field(
        default=None,
        description="For relations, the most common target note type",
    )


class InferenceReport(BaseModel):
    """Inference result with suggested schema definition."""

    note_type: str
    notes_analyzed: int
    field_frequencies: list[FieldFrequencyResponse] = Field(default_factory=list)
    suggested_schema: dict = Field(
        default_factory=dict,
        description="Ready-to-use Picoschema YAML dict",
    )
    suggested_required: list[str] = Field(default_factory=list)
    suggested_optional: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(
        default_factory=list,
        description="Fields below the inclusion threshold",
    )


# --- Drift Response Models ---


class DriftFieldResponse(BaseModel):
    """A field involved in schema drift."""

    name: str
    source: str
    count: int
    total: int
    percentage: float


class DriftReport(BaseModel):
    """Schema drift analysis comparing schema definition to actual usage."""

    note_type: str
    schema_found: bool = Field(
        default=True,
        description="Whether a schema was found for this type",
    )
    new_fields: list[DriftFieldResponse] = Field(
        default_factory=list,
        description="Fields common in notes but not in schema",
    )
    dropped_fields: list[DriftFieldResponse] = Field(
        default_factory=list,
        description="Fields in schema but rare in notes",
    )
    cardinality_changes: list[str] = Field(default_factory=list)
