"""Schema validator for Basic Memory.

Validates a note's observations and relations against a resolved schema definition.
The mapping rules ground schema fields in the existing Basic Memory note format:

  Schema Declaration        -> Grounded In
  -----------------------------------------------
  field: string             -> observation [field] value
  field?(array): string     -> multiple [field] observations
  field?: EntityType        -> relation 'field [[Target]]'
  field?(array): EntityType -> multiple 'field' relations
  field?(enum): [values]    -> observation [field] value where value is in set

Validation is soft by default (warn mode). Unmatched observations and relations
are informational, not errors -- schemas are a subset, not a straitjacket.
"""

from dataclasses import dataclass, field

from basic_memory.schema.parser import SchemaDefinition, SchemaField


# --- Result Data Model ---


@dataclass
class FieldResult:
    """Validation result for a single schema field."""

    field: SchemaField
    status: str  # "present" | "missing" | "enum_mismatch"
    values: list[str] = field(default_factory=list)  # Matched values
    message: str | None = None


@dataclass
class ValidationResult:
    """Complete validation result for a note against a schema."""

    note_identifier: str
    schema_entity: str
    passed: bool  # True if no errors (warnings are OK)
    field_results: list[FieldResult] = field(default_factory=list)
    unmatched_observations: dict[str, int] = field(default_factory=dict)  # category -> count
    unmatched_relations: list[str] = field(default_factory=list)  # relation types not in schema
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --- Validation Logic ---


def validate_note(
    note_identifier: str,
    schema: SchemaDefinition,
    observations: list[tuple[str, str]],
    relations: list[tuple[str, str]],
) -> ValidationResult:
    """Validate a note against a schema definition.

    Args:
        note_identifier: The note's title, permalink, or file path for reporting.
        schema: The resolved SchemaDefinition to validate against.
        observations: List of (category, content) tuples from the note's observations.
        relations: List of (relation_type, target_name) tuples from the note's relations.

    Returns:
        A ValidationResult with per-field results, unmatched items, and warnings/errors.
    """
    result = ValidationResult(
        note_identifier=note_identifier,
        schema_entity=schema.entity,
        passed=True,
    )

    # Build lookup structures from the note's actual content
    obs_by_category = _group_observations(observations)
    rel_by_type = _group_relations(relations)

    # Track which observation categories and relation types are matched by schema fields
    matched_categories: set[str] = set()
    matched_relation_types: set[str] = set()

    # --- Validate each schema field ---
    for schema_field in schema.fields:
        field_result = _validate_field(schema_field, obs_by_category, rel_by_type)
        result.field_results.append(field_result)

        # Track which categories/relation types this field consumed
        if schema_field.is_entity_ref:
            matched_relation_types.add(schema_field.name)
        else:
            matched_categories.add(schema_field.name)

        # --- Generate warnings or errors based on validation mode ---
        # Trigger: field declared in schema but not found in note
        # Why: required missing = warning (or error in strict); optional missing = silent
        # Outcome: only required missing fields produce diagnostics
        if field_result.status == "missing" and schema_field.required:
            msg = _missing_field_message(schema_field)
            if schema.validation_mode == "strict":
                result.errors.append(msg)
                result.passed = False
            else:
                result.warnings.append(msg)

        elif field_result.status == "enum_mismatch":
            msg = field_result.message or f"Field '{schema_field.name}' has invalid enum value"
            if schema.validation_mode == "strict":
                result.errors.append(msg)
                result.passed = False
            else:
                result.warnings.append(msg)

    # --- Collect unmatched observations ---
    for category, values in obs_by_category.items():
        if category not in matched_categories:
            result.unmatched_observations[category] = len(values)

    # --- Collect unmatched relations ---
    for rel_type in rel_by_type:
        if rel_type not in matched_relation_types:
            result.unmatched_relations.append(rel_type)

    return result


# --- Field Validation ---


def _validate_field(
    schema_field: SchemaField,
    obs_by_category: dict[str, list[str]],
    rel_by_type: dict[str, list[str]],
) -> FieldResult:
    """Validate a single schema field against the note's data.

    Entity ref fields map to relations; all other fields map to observations.
    """
    # --- Entity reference fields map to relations ---
    if schema_field.is_entity_ref:
        return _validate_entity_ref_field(schema_field, rel_by_type)

    # --- Enum fields require value membership check ---
    if schema_field.is_enum:
        return _validate_enum_field(schema_field, obs_by_category)

    # --- Scalar and array fields map to observations ---
    return _validate_observation_field(schema_field, obs_by_category)


def _validate_observation_field(
    schema_field: SchemaField,
    obs_by_category: dict[str, list[str]],
) -> FieldResult:
    """Validate a field that maps to observation categories."""
    values = obs_by_category.get(schema_field.name, [])

    if not values:
        return FieldResult(
            field=schema_field,
            status="missing",
            message=_missing_field_message(schema_field),
        )

    return FieldResult(
        field=schema_field,
        status="present",
        values=values,
    )


def _validate_entity_ref_field(
    schema_field: SchemaField,
    rel_by_type: dict[str, list[str]],
) -> FieldResult:
    """Validate a field that maps to relations (entity references)."""
    targets = rel_by_type.get(schema_field.name, [])

    if not targets:
        return FieldResult(
            field=schema_field,
            status="missing",
            message=f"Missing relation: {schema_field.name} (no '{schema_field.name} [[...]]' "
            f"relation found)",
        )

    return FieldResult(
        field=schema_field,
        status="present",
        values=targets,
    )


def _validate_enum_field(
    schema_field: SchemaField,
    obs_by_category: dict[str, list[str]],
) -> FieldResult:
    """Validate an enum field -- value must be in the allowed set."""
    values = obs_by_category.get(schema_field.name, [])

    if not values:
        return FieldResult(
            field=schema_field,
            status="missing",
            message=_missing_field_message(schema_field),
        )

    # Check each value against the allowed enum values
    invalid_values = [v for v in values if v not in schema_field.enum_values]
    if invalid_values:
        allowed = ", ".join(schema_field.enum_values)
        invalid = ", ".join(invalid_values)
        return FieldResult(
            field=schema_field,
            status="enum_mismatch",
            values=values,
            message=f"Field '{schema_field.name}' has invalid value(s): {invalid} "
            f"(allowed: {allowed})",
        )

    return FieldResult(
        field=schema_field,
        status="present",
        values=values,
    )


# --- Helper Functions ---


def _group_observations(observations: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group observation tuples by category."""
    result: dict[str, list[str]] = {}
    for category, content in observations:
        result.setdefault(category, []).append(content)
    return result


def _group_relations(relations: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group relation tuples by relation type."""
    result: dict[str, list[str]] = {}
    for rel_type, target in relations:
        result.setdefault(rel_type, []).append(target)
    return result


def _missing_field_message(schema_field: SchemaField) -> str:
    """Generate a human-readable message for a missing field."""
    kind = "required" if schema_field.required else "optional"

    if schema_field.is_entity_ref:
        return (
            f"Missing {kind} field: {schema_field.name} "
            f"(no '{schema_field.name} [[...]]' relation found)"
        )

    return (
        f"Missing {kind} field: {schema_field.name} "
        f"(expected [{schema_field.name}] observation)"
    )
