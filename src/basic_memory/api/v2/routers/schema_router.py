"""V2 router for schema operations.

Provides endpoints for schema validation, inference, and drift detection.
The schema system validates notes against Picoschema definitions without
introducing any new data model -- it works entirely with existing
observations and relations.

Flow: Entity loaded with eager observations/relations -> convert to tuples -> core functions.
"""

from fastapi import APIRouter, Path, Query

from basic_memory.deps import (
    SearchServiceV2ExternalDep,
    EntityRepositoryV2ExternalDep,
)
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
from basic_memory.schemas.search import SearchQuery
from basic_memory.schema.resolver import resolve_schema
from basic_memory.schema.validator import validate_note
from basic_memory.schema.inference import infer_schema, NoteData
from basic_memory.schema.diff import diff_schema

# Note: No prefix here -- it's added during registration as /v2/{project_id}/schema
router = APIRouter(tags=["schema"])


# --- ORM to core data conversion ---


def _entity_observations(entity: Entity) -> list[tuple[str, str]]:
    """Extract (category, content) tuples from an entity's observations."""
    return [(obs.category, obs.content) for obs in entity.observations]


def _entity_relations(entity: Entity) -> list[tuple[str, str]]:
    """Extract (relation_type, target_name) tuples from an entity's outgoing relations."""
    return [(rel.relation_type, rel.to_name) for rel in entity.outgoing_relations]


def _entity_to_note_data(entity: Entity) -> NoteData:
    """Convert an ORM Entity to a NoteData for inference/diff analysis."""
    return NoteData(
        identifier=entity.permalink or entity.file_path,
        observations=_entity_observations(entity),
        relations=_entity_relations(entity),
        entity_type=entity.entity_type,
    )


def _entity_frontmatter(entity: Entity) -> dict:
    """Build a frontmatter dict from an entity for schema resolution."""
    frontmatter = dict(entity.entity_metadata) if entity.entity_metadata else {}
    if entity.entity_type:
        frontmatter.setdefault("type", entity.entity_type)
    return frontmatter


# --- Validation ---


@router.post("/schema/validate", response_model=ValidationReport)
async def validate_schema(
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    entity_type: str | None = Query(None, description="Entity type to validate"),
    identifier: str | None = Query(None, description="Specific note identifier"),
):
    """Validate notes against their resolved schemas.

    Validates a specific note (by identifier) or all notes of a given type.
    Returns warnings/errors based on the schema's validation mode.
    """
    results: list[NoteValidationResponse] = []

    async def search_fn(query: str) -> list:
        # Search for schema notes, then load full entity_metadata from the entity table.
        # The search index only stores minimal metadata (e.g., {"entity_type": "schema"}),
        # but parse_schema_note needs the full frontmatter with entity/schema/version keys.
        results = await search_service.search(SearchQuery(text=query, types=["schema"]), limit=5)
        frontmatters = []
        for row in results:
            if row.permalink:
                entity = await entity_repository.get_by_permalink(row.permalink)
                if entity:
                    frontmatters.append(_entity_frontmatter(entity))
        return frontmatters

    # --- Single note validation ---
    if identifier:
        entity = await entity_repository.get_by_permalink(identifier)
        if not entity:
            return ValidationReport(entity_type=entity_type, total_notes=0, results=[])

        schema_def = await resolve_schema(_entity_frontmatter(entity), search_fn)
        if schema_def:
            result = validate_note(
                entity.permalink or identifier,
                schema_def,
                _entity_observations(entity),
                _entity_relations(entity),
            )
            results.append(_to_note_validation_response(result))

        return ValidationReport(
            entity_type=entity_type or entity.entity_type,
            total_notes=1,
            valid_count=1 if (results and results[0].passed) else 0,
            warning_count=sum(len(r.warnings) for r in results),
            error_count=sum(len(r.errors) for r in results),
            results=results,
        )

    # --- Batch validation by entity type ---
    entities = await _find_by_entity_type(entity_repository, entity_type) if entity_type else []

    for entity in entities:
        schema_def = await resolve_schema(_entity_frontmatter(entity), search_fn)
        if schema_def:
            result = validate_note(
                entity.permalink or entity.file_path,
                schema_def,
                _entity_observations(entity),
                _entity_relations(entity),
            )
            results.append(_to_note_validation_response(result))

    valid = sum(1 for r in results if r.passed)
    return ValidationReport(
        entity_type=entity_type,
        total_notes=len(results),
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
    entity_type: str = Query(..., description="Entity type to analyze"),
    threshold: float = Query(0.25, description="Minimum frequency for optional fields"),
):
    """Infer a schema from existing notes of a given type.

    Examines observation categories and relation types across all notes
    of the given type. Returns frequency analysis and suggested Picoschema.
    """
    entities = await _find_by_entity_type(entity_repository, entity_type)
    notes_data = [_entity_to_note_data(entity) for entity in entities]

    result = infer_schema(entity_type, notes_data, optional_threshold=threshold)

    return InferenceReport(
        entity_type=result.entity_type,
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


@router.get("/schema/diff/{entity_type}", response_model=DriftReport)
async def diff_schema_endpoint(
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_type: str = Path(..., description="Entity type to check for drift"),
    project_id: str = Path(..., description="Project external UUID"),
):
    """Show drift between a schema definition and actual note usage.

    Compares the existing schema for an entity type against how notes
    of that type are actually structured. Identifies new fields, dropped
    fields, and cardinality changes.
    """

    async def search_fn(query: str) -> list:
        # Search for schema notes, then load full entity_metadata from the entity table.
        # The search index only stores minimal metadata (e.g., {"entity_type": "schema"}),
        # but parse_schema_note needs the full frontmatter with entity/schema/version keys.
        results = await search_service.search(SearchQuery(text=query, types=["schema"]), limit=5)
        frontmatters = []
        for row in results:
            if row.permalink:
                entity = await entity_repository.get_by_permalink(row.permalink)
                if entity:
                    frontmatters.append(_entity_frontmatter(entity))
        return frontmatters

    # Resolve schema by entity type
    schema_frontmatter = {"type": entity_type}
    schema_def = await resolve_schema(schema_frontmatter, search_fn)

    if not schema_def:
        return DriftReport(entity_type=entity_type)

    # Collect all notes of this type
    entities = await _find_by_entity_type(entity_repository, entity_type)
    notes_data = [_entity_to_note_data(entity) for entity in entities]

    result = diff_schema(schema_def, notes_data)

    return DriftReport(
        entity_type=entity_type,
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


async def _find_by_entity_type(
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_type: str,
) -> list[Entity]:
    """Find all entities of a given type using the repository's select pattern."""
    query = entity_repository.select().where(Entity.entity_type == entity_type)
    result = await entity_repository.execute_query(query)
    return list(result.scalars().all())


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
