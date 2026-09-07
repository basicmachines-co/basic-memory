"""Schema system for Basic Memory.

Provides Picoschema-based validation for notes using observation/relation mapping.
Schemas are just notes with type: schema — no new data model, no migration.
"""

from basic_memory.picoschema.parser import (
    SchemaField,
    SchemaDefinition,
    parse_picoschema,
    parse_schema_note,
)
from basic_memory.picoschema.resolver import (
    ResolvedSchema,
    SchemaCandidate,
    resolve_schema,
    resolve_schema_with_source,
)
from basic_memory.picoschema.validator import (
    FieldResult,
    ValidationResult,
    validate_note,
)
from basic_memory.picoschema.inference import (
    FieldFrequency,
    InferenceResult,
    ObservationData,
    RelationData,
    NoteData,
    infer_schema,
    analyze_observations,
    analyze_relations,
)
from basic_memory.picoschema.diff import (
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
    "resolve_schema_with_source",
    "SchemaCandidate",
    "ResolvedSchema",
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
