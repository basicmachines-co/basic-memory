"""Schema inference engine for Basic Memory.

Analyzes notes of a given type and suggests a schema based on observation
and relation frequency. Instead of requiring users to define schemas upfront,
schemas emerge from actual usage patterns:

  Write notes freely -> Patterns emerge -> Crystallize into schema

Frequency thresholds:
  - 95%+ present  -> required field
  - 25%+ present  -> optional field
  - Below 25%     -> excluded from suggestion (but noted)
"""

from collections import Counter
from dataclasses import dataclass, field


# --- Result Data Model ---


@dataclass
class FieldFrequency:
    """Frequency analysis for a single field across notes of a type."""

    name: str
    source: str  # "observation" | "relation"
    count: int  # notes containing this field
    total: int  # total notes analyzed
    percentage: float
    sample_values: list[str] = field(default_factory=list)
    is_array: bool = False  # True if typically appears multiple times per note
    target_type: str | None = None  # For relations, the most common target note type


@dataclass
class InferenceResult:
    """Complete inference result with frequency analysis and suggested schema."""

    note_type: str
    notes_analyzed: int
    field_frequencies: list[FieldFrequency]
    suggested_schema: dict  # Ready-to-use Picoschema YAML dict
    suggested_required: list[str]
    suggested_optional: list[str]
    excluded: list[str]  # Below threshold


# --- Note Data Abstraction ---
# Instead of depending on the ORM Entity model, we accept simple data structures.
# This keeps the inference engine decoupled from the data access layer.


@dataclass
class ObservationData:
    """Lightweight observation for schema analysis. Decoupled from ORM."""

    category: str
    content: str


@dataclass
class RelationData:
    """Lightweight relation for schema analysis. Decoupled from ORM."""

    relation_type: str
    target_name: str
    target_note_type: str | None = None


@dataclass
class NoteData:
    """Minimal note representation for inference analysis.

    Decoupled from ORM models so the inference engine can work with
    any data source (database, files, API responses).
    """

    identifier: str
    observations: list[ObservationData]
    relations: list[RelationData]


# --- Inference Logic ---


def infer_schema(
    note_type: str,
    notes: list[NoteData],
    required_threshold: float = 0.95,
    optional_threshold: float = 0.25,
    max_sample_values: int = 5,
) -> InferenceResult:
    """Analyze notes and suggest a Picoschema definition.

    Examines observation categories and relation types across all provided notes.
    Fields that appear in a high percentage of notes become required; those that
    appear less frequently become optional.

    Args:
        note_type: The note type being analyzed (e.g., "person").
        notes: List of NoteData objects to analyze.
        required_threshold: Frequency at or above which a field is required (default 0.95).
        optional_threshold: Frequency at or above which a field is optional (default 0.25).
        max_sample_values: Maximum number of sample values to include per field.

    Returns:
        An InferenceResult with frequency analysis and suggested Picoschema dict.
    """
    total = len(notes)
    if total == 0:
        return InferenceResult(
            note_type=note_type,
            notes_analyzed=0,
            field_frequencies=[],
            suggested_schema={},
            suggested_required=[],
            suggested_optional=[],
            excluded=[],
        )

    # --- Analyze observation frequencies ---
    obs_frequencies = analyze_observations(notes, total, max_sample_values)

    # --- Analyze relation frequencies ---
    rel_frequencies = analyze_relations(notes, total, max_sample_values)

    # --- Classify fields by threshold ---
    all_frequencies = obs_frequencies + rel_frequencies
    suggested_required: list[str] = []
    suggested_optional: list[str] = []
    excluded: list[str] = []

    for freq in all_frequencies:
        if freq.percentage >= required_threshold:
            suggested_required.append(freq.name)
        elif freq.percentage >= optional_threshold:
            suggested_optional.append(freq.name)
        else:
            excluded.append(freq.name)

    # --- Build suggested Picoschema dict ---
    suggested_schema = _build_picoschema_dict(
        all_frequencies, required_threshold, optional_threshold
    )

    return InferenceResult(
        note_type=note_type,
        notes_analyzed=total,
        field_frequencies=all_frequencies,
        suggested_schema=suggested_schema,
        suggested_required=suggested_required,
        suggested_optional=suggested_optional,
        excluded=excluded,
    )


# --- Observation Analysis ---


def analyze_observations(
    notes: list[NoteData],
    total: int,
    max_sample_values: int,
) -> list[FieldFrequency]:
    """Count observation category frequencies across notes.

    A category is counted once per note (presence), not per occurrence.
    Array detection: if a category appears multiple times in a single note
    in more than half the notes where it appears, it's flagged as an array.
    """
    # Count how many notes contain each category (presence per note)
    category_note_count: Counter[str] = Counter()
    # Count how many notes have multiple occurrences (for array detection)
    category_multi_count: Counter[str] = Counter()
    # Collect sample values
    category_samples: dict[str, list[str]] = {}

    for note in notes:
        # Group observations by category within this note
        note_categories: dict[str, list[str]] = {}
        for obs in note.observations:
            note_categories.setdefault(obs.category, []).append(obs.content)

        for category, values in note_categories.items():
            category_note_count[category] += 1
            if len(values) > 1:
                category_multi_count[category] += 1

            # Collect sample values (deduplicated)
            samples = category_samples.setdefault(category, [])
            for v in values:
                if v not in samples and len(samples) < max_sample_values:
                    samples.append(v)

    # Build FieldFrequency objects
    frequencies: list[FieldFrequency] = []
    for category, count in category_note_count.most_common():
        # Array detection: if more than half of notes with this category have
        # multiple occurrences, treat it as an array field
        multi_count = category_multi_count.get(category, 0)
        is_array = multi_count > (count / 2)

        frequencies.append(
            FieldFrequency(
                name=category,
                source="observation",
                count=count,
                total=total,
                percentage=count / total,
                sample_values=category_samples.get(category, []),
                is_array=is_array,
            )
        )

    return frequencies


# --- Relation Analysis ---


def analyze_relations(
    notes: list[NoteData],
    total: int,
    max_sample_values: int,
) -> list[FieldFrequency]:
    """Count relation type frequencies across notes.

    Similar to observations, a relation type is counted once per note.
    Array detection follows the same logic.
    """
    rel_note_count: Counter[str] = Counter()
    rel_multi_count: Counter[str] = Counter()
    rel_samples: dict[str, list[str]] = {}
    # Track target entity types to suggest the type in the schema
    rel_target_types: dict[str, Counter[str]] = {}

    for note in notes:
        note_rels: dict[str, list[str]] = {}
        note_rel_objects: dict[str, list[RelationData]] = {}
        for rel in note.relations:
            note_rels.setdefault(rel.relation_type, []).append(rel.target_name)
            note_rel_objects.setdefault(rel.relation_type, []).append(rel)

        for rel_type, targets in note_rels.items():
            rel_note_count[rel_type] += 1
            if len(targets) > 1:
                rel_multi_count[rel_type] += 1

            samples = rel_samples.setdefault(rel_type, [])
            for t in targets:
                if t not in samples and len(samples) < max_sample_values:
                    samples.append(t)

            # Track target entity types from individual relations (not the source note)
            target_counter = rel_target_types.setdefault(rel_type, Counter())
            for rel in note_rel_objects[rel_type]:
                if rel.target_note_type:
                    target_counter[rel.target_note_type] += 1

    frequencies: list[FieldFrequency] = []
    for rel_type, count in rel_note_count.most_common():
        multi_count = rel_multi_count.get(rel_type, 0)
        is_array = multi_count > (count / 2)

        # Determine most common target type
        target_counter = rel_target_types.get(rel_type, Counter())
        most_common_target = target_counter.most_common(1)[0][0] if target_counter else None

        frequencies.append(
            FieldFrequency(
                name=rel_type,
                source="relation",
                count=count,
                total=total,
                percentage=count / total,
                sample_values=rel_samples.get(rel_type, []),
                is_array=is_array,
                target_type=most_common_target,
            )
        )

    return frequencies


# --- Schema Generation ---


def _build_picoschema_dict(
    frequencies: list[FieldFrequency],
    required_threshold: float,
    optional_threshold: float,
) -> dict:
    """Build a Picoschema YAML dict from field frequencies.

    Only includes fields at or above the optional threshold.
    """
    schema: dict = {}

    for freq in frequencies:
        if freq.percentage < optional_threshold:
            continue

        is_required = freq.percentage >= required_threshold

        # --- Build the field key ---
        key = freq.name
        if not is_required:
            key += "?"
        if freq.is_array:
            key += "(array)"

        # --- Build the field value ---
        if freq.source == "relation":
            # Relations become entity reference fields
            target = freq.target_type or "string"
            # Capitalize first letter for entity ref convention
            target = target[0].upper() + target[1:] if target != "string" else "string"
            schema[key] = target
        else:
            schema[key] = "string"

    return schema
