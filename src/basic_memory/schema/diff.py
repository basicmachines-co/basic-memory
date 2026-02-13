"""Schema diff for Basic Memory.

Compares a schema definition against actual note usage to detect drift.
Drift happens naturally as notes evolve -- new observation categories appear,
old ones fall out of use, single-value fields become multi-value.

The diff engine reuses inference analysis internally, comparing inferred
frequencies against the declared schema fields to surface:
  - New fields: common in notes but not declared in schema
  - Dropped fields: declared in schema but rare in actual notes
  - Cardinality changes: field changed from single to array or vice versa
"""

from dataclasses import dataclass, field

from basic_memory.schema.inference import (
    FieldFrequency,
    NoteData,
    analyze_observations,
    analyze_relations,
)
from basic_memory.schema.parser import SchemaDefinition


@dataclass
class SchemaDrift:
    """Result of comparing a schema against actual note usage."""

    new_fields: list[FieldFrequency] = field(default_factory=list)
    dropped_fields: list[FieldFrequency] = field(default_factory=list)
    cardinality_changes: list[str] = field(default_factory=list)


def diff_schema(
    schema: SchemaDefinition,
    notes: list[NoteData],
    new_field_threshold: float = 0.25,
    dropped_field_threshold: float = 0.10,
) -> SchemaDrift:
    """Compare a schema against actual note usage to detect drift.

    Args:
        schema: The current schema definition.
        notes: List of NoteData objects representing actual notes.
        new_field_threshold: Frequency above which an undeclared field is considered
            "new" and worth adding to the schema.
        dropped_field_threshold: Frequency below which a declared field is considered
            "dropped" and worth removing from the schema.

    Returns:
        A SchemaDrift describing the differences between schema and reality.
    """
    total = len(notes)
    if total == 0:
        return SchemaDrift()

    # --- Analyze actual usage ---
    obs_frequencies = analyze_observations(notes, total, max_sample_values=3)
    rel_frequencies = analyze_relations(notes, total, max_sample_values=3)

    # Build lookup from schema fields
    schema_field_names = {f.name for f in schema.fields}

    # Build lookup from actual frequencies
    obs_freq_by_name = {f.name: f for f in obs_frequencies}
    rel_freq_by_name = {f.name: f for f in rel_frequencies}
    all_freq_by_name = {**obs_freq_by_name, **rel_freq_by_name}

    result = SchemaDrift()

    # --- Detect new fields ---
    # Fields that appear frequently in notes but aren't declared in the schema
    for freq in obs_frequencies + rel_frequencies:
        if freq.name not in schema_field_names and freq.percentage >= new_field_threshold:
            result.new_fields.append(freq)

    # --- Detect dropped fields ---
    # Fields declared in the schema but rarely appearing in actual notes
    for schema_field in schema.fields:
        freq = all_freq_by_name.get(schema_field.name)
        if freq is None:
            # Field doesn't appear at all in any note
            result.dropped_fields.append(
                FieldFrequency(
                    name=schema_field.name,
                    source="relation" if schema_field.is_entity_ref else "observation",
                    count=0,
                    total=total,
                    percentage=0.0,
                )
            )
        elif freq.percentage < dropped_field_threshold:
            result.dropped_fields.append(freq)

    # --- Detect cardinality changes ---
    # Fields where the schema says single but usage shows array, or vice versa
    for schema_field in schema.fields:
        freq = all_freq_by_name.get(schema_field.name)
        if freq is None:
            continue

        if schema_field.is_array and not freq.is_array:
            result.cardinality_changes.append(
                f"{schema_field.name}: schema declares array but usage is typically single-value"
            )
        elif not schema_field.is_array and freq.is_array:
            result.cardinality_changes.append(
                f"{schema_field.name}: schema declares single-value but usage is typically array"
            )

    return result
