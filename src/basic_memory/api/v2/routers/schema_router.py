"""V2 router for schema operations.

Provides endpoints for schema validation, inference, and drift detection.
The schema system validates notes against Picoschema definitions without
introducing any new data model -- it works entirely with existing
observations and relations.

Flow: Entity loaded with eager observations/relations -> convert to tuples -> core functions.
"""

from pathlib import Path as FilePath

import frontmatter
from fastapi import APIRouter, Path, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.deps import (
    EntityRepositoryV2ExternalDep,
    FileServiceV2ExternalDep,
    LinkResolverV2ExternalDep,
    SessionDep,
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
    TypeValidationSummary,
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
    """Build a frontmatter dict from an entity's database metadata.

    Used for the notes being validated — their type and schema ref are
    unlikely to change between syncs.
    """
    fm = dict(entity.entity_metadata) if entity.entity_metadata else {}
    if entity.note_type:
        fm.setdefault("type", entity.note_type)
    return fm


async def _schema_frontmatter_from_file(
    file_service: FileServiceV2ExternalDep,
    entity: Entity,
) -> dict:
    """Read a schema entity's frontmatter directly from its file.

    Schema definitions (field declarations, validation mode) are the source
    of truth for validation. Reading from the file ensures schema-validate
    always uses the latest settings, even when the file watcher hasn't
    synced changes to entity_metadata in the database.
    """
    try:
        content = await file_service.read_file_content(entity.file_path)
        post = frontmatter.loads(content)
        metadata = dict(post.metadata)

        # Trigger: file is mid-edit and missing required schema fields
        # Why: parse_schema_note() raises ValueError for missing entity/schema,
        #   which would turn validation into a 500 response
        # Outcome: fall back to last-known-good database metadata
        if not metadata.get("entity") or not isinstance(metadata.get("schema"), dict):
            logger.warning(
                "Schema file has incomplete frontmatter, falling back to database metadata",
                file_path=entity.file_path,
            )
            return _entity_frontmatter(entity)

        return metadata
    except Exception:
        # Trigger: file is missing, unreadable, or has malformed frontmatter
        # Why: fall back to database metadata rather than failing validation entirely
        # Outcome: behaves like before this change — uses potentially stale data
        logger.warning(
            "Failed to read schema file, falling back to database metadata",
            file_path=entity.file_path,
        )
        return _entity_frontmatter(entity)


# --- Validation ---


@router.post("/schema/validate", response_model=ValidationReport)
async def validate_schema(
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    link_resolver: LinkResolverV2ExternalDep,
    session: SessionDep,
    project_id: str = Path(..., description="Project external UUID"),
    note_type: str | None = Query(None, description="Note type to validate"),
    identifier: str | None = Query(None, description="Specific note identifier"),
):
    """Validate notes against their resolved schemas.

    Validates a specific note (by identifier), all notes of a given type, or —
    when neither is provided — all notes of every type that has a schema
    defined, with a per-type breakdown in type_summaries.
    Returns warnings/errors based on the schema's validation mode.

    Schema definitions are read directly from their files to ensure the
    latest settings (validation mode, field declarations) are always used,
    even when file changes haven't been synced to the database yet.
    """
    results: list[NoteValidationResponse] = []

    # --- Single note validation ---
    if identifier:
        # Resolve identifier flexibly (permalink, title, path, fuzzy)
        # to match how read_note and other tools resolve identifiers
        entity = await link_resolver.resolve_link(identifier, session=session)
        if not entity:
            return ValidationReport(note_type=note_type, total_notes=0, total_entities=0)

        frontmatter = _entity_frontmatter(entity)
        schema_ref = frontmatter.get("schema")

        async def search_fn(query: str) -> list[dict]:
            entities = await _find_schema_entities(
                session,
                entity_repository,
                query,
                allow_reference_match=isinstance(schema_ref, str) and query == schema_ref,
            )
            return [await _schema_frontmatter_from_file(file_service, e) for e in entities]

        schema_def = await resolve_schema(frontmatter, search_fn)
        if schema_def:
            result = validate_note(
                entity.title or entity.permalink or identifier,
                schema_def,
                _entity_observations(entity),
                _entity_relations(entity),
                frontmatter=frontmatter,
            )
            results.append(_to_note_validation_response(result))

        return ValidationReport(
            note_type=note_type or entity.note_type,
            total_notes=len(results),
            total_entities=1,
            valid_count=1 if (results and results[0].passed) else 0,
            warning_count=sum(len(r.warnings) for r in results),
            error_count=sum(len(r.errors) for r in results),
            results=results,
        )

    # --- Batch validation by note type ---
    if note_type:
        entities = await _find_by_note_type(session, entity_repository, note_type)
        results = await _validate_note_entities(session, entity_repository, file_service, entities)
        return ValidationReport(
            note_type=note_type,
            total_notes=len(results),
            total_entities=len(entities),
            valid_count=sum(1 for r in results if r.passed),
            warning_count=sum(len(r.warnings) for r in results),
            error_count=sum(len(r.errors) for r in results),
            results=results,
        )

    # --- All-types validation ---
    # Trigger: neither identifier nor note_type was provided
    # Why: schema_validate() with no arguments should check every note type that
    #   has a schema defined instead of returning an empty report (#1013)
    # Outcome: aggregated report with a per-type breakdown in type_summaries
    covered_types = await _schema_covered_note_types(session, entity_repository)

    type_summaries: list[TypeValidationSummary] = []
    total_entities = 0
    for target_type, stored_note_types in covered_types.items():
        entities = []
        for stored_type in stored_note_types:
            entities.extend(await _find_by_note_type(session, entity_repository, stored_type))
        type_results = await _validate_note_entities(
            session, entity_repository, file_service, entities
        )
        type_summaries.append(
            TypeValidationSummary(
                note_type=target_type,
                total_notes=len(type_results),
                total_entities=len(entities),
                valid_count=sum(1 for r in type_results if r.passed),
                warning_count=sum(len(r.warnings) for r in type_results),
                error_count=sum(len(r.errors) for r in type_results),
            )
        )
        results.extend(type_results)
        total_entities += len(entities)

    return ValidationReport(
        note_type=None,
        total_notes=len(results),
        total_entities=total_entities,
        valid_count=sum(1 for r in results if r.passed),
        warning_count=sum(len(r.warnings) for r in results),
        error_count=sum(len(r.errors) for r in results),
        results=results,
        type_summaries=type_summaries,
    )


# --- Inference ---


@router.post("/schema/infer", response_model=InferenceReport)
async def infer_schema_endpoint(
    entity_repository: EntityRepositoryV2ExternalDep,
    session: SessionDep,
    project_id: str = Path(..., description="Project external UUID"),
    note_type: str = Query(..., description="Note type to analyze"),
    threshold: float = Query(0.25, description="Minimum frequency for optional fields"),
):
    """Infer a schema from existing notes of a given type.

    Examines observation categories and relation types across all notes
    of the given type. Returns frequency analysis and suggested Picoschema.
    """
    entities = await _find_by_note_type(session, entity_repository, note_type)
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
    file_service: FileServiceV2ExternalDep,
    session: SessionDep,
    note_type: str = Path(..., description="Note type to check for drift"),
    project_id: str = Path(..., description="Project external UUID"),
):
    """Show drift between a schema definition and actual note usage.

    Compares the existing schema for an entity type against how notes
    of that type are actually structured. Identifies new fields, dropped
    fields, and cardinality changes.
    """

    async def search_fn(query: str) -> list[dict]:
        entities = await _find_schema_entities(session, entity_repository, query)
        return [await _schema_frontmatter_from_file(file_service, e) for e in entities]

    # Resolve schema by note type
    schema_frontmatter = {"type": note_type}
    schema_def = await resolve_schema(schema_frontmatter, search_fn)

    if not schema_def:
        return DriftReport(note_type=note_type, schema_found=False)

    # Collect all notes of this type
    entities = await _find_by_note_type(session, entity_repository, note_type)
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


async def _validate_note_entities(
    session: AsyncSession,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    entities: list[Entity],
) -> list[NoteValidationResponse]:
    """Validate a batch of note entities against their resolved schemas.

    Entities whose frontmatter resolves to no schema are skipped, which is why
    a report's total_notes can be lower than its total_entities.
    """
    results: list[NoteValidationResponse] = []

    for entity in entities:
        frontmatter = _entity_frontmatter(entity)
        schema_ref = frontmatter.get("schema")

        async def search_fn(query: str) -> list[dict]:
            found = await _find_schema_entities(
                session,
                entity_repository,
                query,
                allow_reference_match=isinstance(schema_ref, str) and query == schema_ref,
            )
            return [await _schema_frontmatter_from_file(file_service, e) for e in found]

        schema_def = await resolve_schema(frontmatter, search_fn)
        if schema_def:
            result = validate_note(
                entity.title or entity.permalink or entity.file_path,
                schema_def,
                _entity_observations(entity),
                _entity_relations(entity),
                frontmatter=frontmatter,
            )
            results.append(_to_note_validation_response(result))

    return results


async def _schema_covered_note_types(
    session: AsyncSession,
    entity_repository: EntityRepositoryV2ExternalDep,
) -> dict[str, list[str]]:
    """Map each schema-declared target type to the stored note_type values it covers.

    Schema notes declare their target via the frontmatter `entity` field. Stored
    note types are snake_case while schema authors may write "Person" or "person",
    so both sides are compared through generate_permalink normalization — the same
    matching rule _find_schema_entities uses for implicit type lookup. Targets with
    no matching notes map to an empty list so they still appear in the report.
    """
    schema_query = entity_repository.select().where(Entity.note_type == "schema")
    schema_result = await entity_repository.execute_query(session, schema_query)

    # normalized target -> raw value as the schema author wrote it (first seen wins)
    targets: dict[str, str] = {}
    for schema_entity in schema_result.scalars().all():
        target = (schema_entity.entity_metadata or {}).get("entity")
        if isinstance(target, str) and target:
            targets.setdefault(generate_permalink(target), target)

    # Column-only select: skip eager-load options, they only apply to full-entity queries
    type_query = entity_repository.select(Entity.note_type).distinct()
    type_result = await entity_repository.execute_query(
        session, type_query, use_query_options=False
    )
    stored_types = [t for t in type_result.scalars().all() if t]

    return {
        raw: [t for t in stored_types if generate_permalink(t) == normalized]
        for normalized, raw in sorted(targets.items())
    }


async def _find_by_note_type(
    session: AsyncSession,
    entity_repository: EntityRepositoryV2ExternalDep,
    note_type: str,
) -> list[Entity]:
    """Find all entities of a given type using the repository's select pattern."""
    query = entity_repository.select().where(Entity.note_type == note_type)
    result = await entity_repository.execute_query(session, query)
    return list(result.scalars().all())


async def _find_schema_entities(
    session: AsyncSession,
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
    result = await entity_repository.execute_query(session, query)
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
