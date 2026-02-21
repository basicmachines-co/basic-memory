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

from dataclasses import dataclass, field as dataclass_field

from basic_memory.schema.inference import ObservationData, RelationData
from basic_memory.schema.parser import SchemaDefinition, SchemaField


# --- Result Data Model ---


@dataclass
class FieldResult:
    """Validation result for a single schema field."""

    field: SchemaField
    status: str  # "present" | "missing" | "enum_mismatch"
    values: list[str] = dataclass_field(default_factory=list)  # Matched values
    message: str | None = None


@dataclass
class ValidationResult:
    """Complete validation result for a note against a schema."""

    note_identifier: str
    schema_entity: str
    passed: bool  # True if no errors (warnings are OK)
    field_results: list[FieldResult] = dataclass_field(default_factory=list)
    unmatched_observations: dict[str, int] = dataclass_field(default_factory=dict)  # cat -> count
    unmatched_relations: list[str] = dataclass_field(default_factory=list)  # types not in schema
    warnings: list[str] = dataclass_field(default_factory=list)
    errors: list[str] = dataclass_field(default_factory=list)


# --- Validation Logic ---


def validate_note(
    note_identifier: str,
    schema: SchemaDefinition,
    observations: list[ObservationData],
    relations: list[RelationData],
    frontmatter: dict | None = None,
) -> ValidationResult:
    """Validate a note against a schema definition.

    Args:
        note_identifier: The note's title, permalink, or file path for reporting.
        schema: The resolved SchemaDefinition to validate against.
        observations: List of ObservationData from the note's observations.
        relations: List of RelationData from the note's relations.
        frontmatter: The note's frontmatter dict for settings.frontmatter validation.

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

    # --- Validate frontmatter fields ---
    # Trigger: schema has frontmatter_fields and caller provided frontmatter dict
    # Why: settings.frontmatter rules validate metadata keys like tags, status
    # Outcome: frontmatter fields produce the same FieldResult/warning/error as content fields
    if frontmatter is not None and schema.frontmatter_fields:
        for fm_field in schema.frontmatter_fields:
            field_result = _validate_frontmatter_field(fm_field, frontmatter)
            result.field_results.append(field_result)

            if field_result.status == "missing" and fm_field.required:
                msg = f"Missing required frontmatter key: {fm_field.name}"
                if schema.validation_mode == "strict":
                    result.errors.append(msg)
                    result.passed = False
                else:
                    result.warnings.append(msg)

            elif field_result.status == "enum_mismatch":
                msg = field_result.message or (
                    f"Frontmatter key '{fm_field.name}' has invalid enum value"
                )
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


# --- Frontmatter Field Validation ---


def _validate_frontmatter_field(
    schema_field: SchemaField,
    frontmatter: dict,
) -> FieldResult:
    """Validate a single frontmatter key against a schema field declaration.

    Checks presence and, for enum fields, value membership. Array fields
    collect all list items as string values.
    """
    value = frontmatter.get(schema_field.name)

    if value is None:
        return FieldResult(
            field=schema_field,
            status="missing",
            message=f"Missing frontmatter key: {schema_field.name}",
        )

    # --- Enum validation ---
    if schema_field.is_enum:
        str_value = str(value)
        if str_value not in schema_field.enum_values:
            allowed = ", ".join(schema_field.enum_values)
            return FieldResult(
                field=schema_field,
                status="enum_mismatch",
                values=[str_value],
                message=f"Frontmatter key '{schema_field.name}' has invalid value: "
                f"{str_value} (allowed: {allowed})",
            )
        return FieldResult(
            field=schema_field,
            status="present",
            values=[str_value],
        )

    # --- Array / list values ---
    if isinstance(value, list):
        return FieldResult(
            field=schema_field,
            status="present",
            values=[str(v) for v in value],
        )

    # --- Scalar values ---
    return FieldResult(
        field=schema_field,
        status="present",
        values=[str(value)],
    )


# --- Helper Functions ---


def _group_observations(observations: list[ObservationData]) -> dict[str, list[str]]:
    """Group observations by category."""
    result: dict[str, list[str]] = {}
    for obs in observations:
        result.setdefault(obs.category, []).append(obs.content)
    return result


def _group_relations(relations: list[RelationData]) -> dict[str, list[str]]:
    """Group relations by relation type."""
    result: dict[str, list[str]] = {}
    for rel in relations:
        result.setdefault(rel.relation_type, []).append(rel.target_name)
    return result


def _missing_field_message(schema_field: SchemaField) -> str:
    """Generate a human-readable message for a missing field."""
    kind = "required" if schema_field.required else "optional"

    if schema_field.is_entity_ref:
        return (
            f"Missing {kind} field: {schema_field.name} "
            f"(no '{schema_field.name} [[...]]' relation found)"
        )

    return f"Missing {kind} field: {schema_field.name} (expected [{schema_field.name}] observation)"
