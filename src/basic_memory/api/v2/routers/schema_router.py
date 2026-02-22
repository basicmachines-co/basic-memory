"""V2 router for schema operations.

Provides endpoints for schema validation, inference, and drift detection.
The schema system validates notes against Picoschema definitions without
introducing any new data model -- it works entirely with existing
observations and relations.

Flow: Entity loaded with eager observations/relations -> convert to tuples -> core functions.
"""

from pathlib import Path as FilePath

from fastapi import APIRouter, Path, Query

from basic_memory.deps import EntityRepositoryV2ExternalDep
from basic_memory.models.knowledge import Entity
from basic_memory.schemas.schema import (
    ValidationReport,
    InferenceReport,
    DriftReport,
    NoteValidationResponse,
    FieldResultResponse,
    FieldFrequencyResponse,
    DriftFieldResponse,
)
from basic_memory.schema.resolver import resolve_schema
from basic_memory.schema.validator import validate_note
from basic_memory.schema.inference import infer_schema, NoteData, ObservationData, RelationData
from basic_memory.schema.diff import diff_schema
from basic_memory.utils import generate_permalink

# Note: No prefix here -- it's added during registration as /v2/{project_id}/schema
router = APIRouter(tags=["schema"])


# --- ORM to core data conversion ---


def _entity_observations(entity: Entity) -> list[ObservationData]:
    """Extract ObservationData from an entity's observations."""
    return [ObservationData(obs.category, obs.content) for obs in entity.observations]


def _entity_relations(entity: Entity) -> list[RelationData]:
    """Extract RelationData from an entity's outgoing relations.

    Carries the target entity's type on each relation so the inference engine
    can suggest correct types (e.g. works_at -> Organization, not the source type).
    """
    return [
        RelationData(
            relation_type=rel.relation_type,
            target_name=rel.to_name,
            target_note_type=rel.to_entity.note_type if rel.to_entity else None,
        )
        for rel in entity.outgoing_relations
    ]


def _entity_to_note_data(entity: Entity) -> NoteData:
    """Convert an ORM Entity to a NoteData for inference/diff analysis."""
    return NoteData(
        identifier=entity.permalink or entity.file_path,
        observations=_entity_observations(entity),
        relations=_entity_relations(entity),
    )


def _entity_frontmatter(entity: Entity) -> dict:
    """Build a frontmatter dict from an entity for schema resolution."""
    frontmatter = dict(entity.entity_metadata) if entity.entity_metadata else {}
    if entity.note_type:
        frontmatter.setdefault("type", entity.note_type)
    return frontmatter


# --- Validation ---


@router.post("/schema/validate", response_model=ValidationReport)
async def validate_schema(
    entity_repository: EntityRepositoryV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    note_type: str | None = Query(None, description="Note type to validate"),
    identifier: str | None = Query(None, description="Specific note identifier"),
):
    """Validate notes against their resolved schemas.

    Validates a specific note (by identifier) or all notes of a given type.
    Returns warnings/errors based on the schema's validation mode.
    """
    results: list[NoteValidationResponse] = []

    # --- Single note validation ---
    if identifier:
        entity = await entity_repository.get_by_permalink(identifier)
        if not entity:
            return ValidationReport(note_type=note_type, total_notes=0, results=[])

        frontmatter = _entity_frontmatter(entity)
        schema_ref = frontmatter.get("schema")

        async def search_fn(query: str) -> list[dict]:
            entities = await _find_schema_entities(
                entity_repository,
                query,
                allow_reference_match=isinstance(schema_ref, str) and query == schema_ref,
            )
            return [_entity_frontmatter(e) for e in entities]

        schema_def = await resolve_schema(frontmatter, search_fn)
        if schema_def:
            result = validate_note(
                entity.permalink or identifier,
                schema_def,
                _entity_observations(entity),
                _entity_relations(entity),
                frontmatter=frontmatter,
            )
            results.append(_to_note_validation_response(result))

        return ValidationReport(
            note_type=note_type or entity.note_type,
            total_notes=1,
            valid_count=1 if (results and results[0].passed) else 0,
            warning_count=sum(len(r.warnings) for r in results),
            error_count=sum(len(r.errors) for r in results),
            results=results,
        )

    # --- Batch validation by note type ---
    entities = await _find_by_note_type(entity_repository, note_type) if note_type else []

    for entity in entities:
        frontmatter = _entity_frontmatter(entity)
        schema_ref = frontmatter.get("schema")

        async def search_fn(query: str) -> list[dict]:
            entities = await _find_schema_entities(
                entity_repository,
                query,
                allow_reference_match=isinstance(schema_ref, str) and query == schema_ref,
            )
            return [_entity_frontmatter(e) for e in entities]

        schema_def = await resolve_schema(frontmatter, search_fn)
        if schema_def:
            result = validate_note(
                entity.permalink or entity.file_path,
                schema_def,
                _entity_observations(entity),
                _entity_relations(entity),
                frontmatter=frontmatter,
            )
            results.append(_to_note_validation_response(result))

    valid = sum(1 for r in results if r.passed)
    return ValidationReport(
        note_type=note_type,
        total_notes=len(results),
        total_entities=len(entities),
        valid_count=valid,
        warning_count=sum(len(r.warnings) for r in results),
        error_count=sum(len(r.errors) for r in results),
        results=results,
    )


# --- Inference ---


@router.post("/schema/infer", response_model=InferenceReport)
async def infer_schema_endpoint(
    entity_repository: EntityRepositoryV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    note_type: str = Query(..., description="Note type to analyze"),
    threshold: float = Query(0.25, description="Minimum frequency for optional fields"),
):
    """Infer a schema from existing notes of a given type.

    Examines observation categories and relation types across all notes
    of the given type. Returns frequency analysis and suggested Picoschema.
    """
    entities = await _find_by_note_type(entity_repository, note_type)
    notes_data = [_entity_to_note_data(entity) for entity in entities]

    result = infer_schema(note_type, notes_data, optional_threshold=threshold)

    return InferenceReport(
        note_type=result.note_type,
        notes_analyzed=result.notes_analyzed,
        field_frequencies=[
            FieldFrequencyResponse(
                name=f.name,
                source=f.source,
                count=f.count,
                total=f.total,
                percentage=f.percentage,
                sample_values=f.sample_values,
                is_array=f.is_array,
                target_type=f.target_type,
            )
            for f in result.field_frequencies
        ],
        suggested_schema=result.suggested_schema,
        suggested_required=result.suggested_required,
        suggested_optional=result.suggested_optional,
        excluded=result.excluded,
    )


# --- Drift Detection ---


@router.get("/schema/diff/{note_type}", response_model=DriftReport)
async def diff_schema_endpoint(
    entity_repository: EntityRepositoryV2ExternalDep,
    note_type: str = Path(..., description="Note type to check for drift"),
    project_id: str = Path(..., description="Project external UUID"),
):
    """Show drift between a schema definition and actual note usage.

    Compares the existing schema for an entity type against how notes
    of that type are actually structured. Identifies new fields, dropped
    fields, and cardinality changes.
    """

    async def search_fn(query: str) -> list[dict]:
        entities = await _find_schema_entities(entity_repository, query)
        return [_entity_frontmatter(e) for e in entities]

    # Resolve schema by note type
    schema_frontmatter = {"type": note_type}
    schema_def = await resolve_schema(schema_frontmatter, search_fn)

    if not schema_def:
        return DriftReport(note_type=note_type, schema_found=False)

    # Collect all notes of this type
    entities = await _find_by_note_type(entity_repository, note_type)
    notes_data = [_entity_to_note_data(entity) for entity in entities]

    result = diff_schema(schema_def, notes_data)

    return DriftReport(
        note_type=note_type,
        new_fields=[
            DriftFieldResponse(
                name=f.name,
                source=f.source,
                count=f.count,
                total=f.total,
                percentage=f.percentage,
            )
            for f in result.new_fields
        ],
        dropped_fields=[
            DriftFieldResponse(
                name=f.name,
                source=f.source,
                count=f.count,
                total=f.total,
                percentage=f.percentage,
            )
            for f in result.dropped_fields
        ],
        cardinality_changes=result.cardinality_changes,
    )


# --- Helpers ---


async def _find_by_note_type(
    entity_repository: EntityRepositoryV2ExternalDep,
    note_type: str,
) -> list[Entity]:
    """Find all entities of a given type using the repository's select pattern."""
    query = entity_repository.select().where(Entity.note_type == note_type)
    result = await entity_repository.execute_query(query)
    return list(result.scalars().all())


async def _find_schema_entities(
    entity_repository: EntityRepositoryV2ExternalDep,
    target_note_type: str,
    *,
    allow_reference_match: bool = False,
) -> list[Entity]:
    """Find schema entities for resolver lookups.

    Resolution strategy:
    1) Always try exact entity_metadata['entity'] match (for implicit type lookup
       and explicit references that use entity names)
    2) Only when allow_reference_match=True and no entity match was found, try
       exact reference matching by title/permalink (explicit schema references)
    """
    query = entity_repository.select().where(Entity.note_type == "schema")
    result = await entity_repository.execute_query(query)
    entities = list(result.scalars().all())

    normalized_target = generate_permalink(target_note_type)

    entity_matches = [
        e
        for e in entities
        if e.entity_metadata
        and isinstance(e.entity_metadata.get("entity"), str)
        and generate_permalink(e.entity_metadata["entity"]) == normalized_target
    ]
    if entity_matches:
        return entity_matches

    if not allow_reference_match:
        return []

    reference_matches: list[Entity] = []
    for entity in entities:
        candidate_refs: list[str] = []
        if entity.title:
            candidate_refs.append(entity.title)
        if entity.permalink:
            candidate_refs.append(entity.permalink)
            candidate_refs.append(FilePath(entity.permalink).name)

        if any(generate_permalink(ref) == normalized_target for ref in candidate_refs):
            reference_matches.append(entity)

    return reference_matches


def _to_note_validation_response(result) -> NoteValidationResponse:
    """Convert a core ValidationResult to a Pydantic response model."""
    return NoteValidationResponse(
        note_identifier=result.note_identifier,
        schema_entity=result.schema_entity,
        passed=result.passed,
        field_results=[
            FieldResultResponse(
                field_name=fr.field.name,
                field_type=fr.field.type,
                required=fr.field.required,
                status=fr.status,
                values=fr.values,
                message=fr.message,
            )
            for fr in result.field_results
        ],
        unmatched_observations=result.unmatched_observations,
        unmatched_relations=result.unmatched_relations,
        warnings=result.warnings,
        errors=result.errors,
    )
