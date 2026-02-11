"""Schema system for Basic Memory.

Provides Picoschema-based validation for notes using observation/relation mapping.
Schemas are just notes with type: schema — no new data model, no migration.
"""

from basic_memory.schema.parser import (
    SchemaField,
    SchemaDefinition,
    parse_picoschema,
    parse_schema_note,
)
from basic_memory.schema.resolver import resolve_schema
from basic_memory.schema.validator import (
    FieldResult,
    ValidationResult,
    validate_note,
)
from basic_memory.schema.inference import (
    FieldFrequency,
    InferenceResult,
    ObservationData,
    RelationData,
    NoteData,
    infer_schema,
    analyze_observations,
    analyze_relations,
)
from basic_memory.schema.diff import (
    SchemaDrift,
    diff_schema,
)

__all__ = [
    # Parser
    "SchemaField",
    "SchemaDefinition",
    "parse_picoschema",
    "parse_schema_note",
    # Resolver
    "resolve_schema",
    # Validator
    "FieldResult",
    "ValidationResult",
    "validate_note",
    # Inference
    "FieldFrequency",
    "InferenceResult",
    "ObservationData",
    "RelationData",
    "NoteData",
    "infer_schema",
    "analyze_observations",
    "analyze_relations",
    # Diff
    "SchemaDrift",
    "diff_schema",
]
