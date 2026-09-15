"""Database-scoped search for trusted API callers supplying effective project IDs.

Authorization belongs to Cloud. This endpoint never discovers an implicit scope.
Caching is deliberately bypassed: the existing cache owns one project generation.
Milvus remains available through project search; this route supports semantic
retrieval only over shared sqlite-vec/pgvector storage, without project fan-out.
"""

from dataclasses import replace

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select, tuple_

from basic_memory import db
from basic_memory.api.v2.utils import _temporal_result_metadata
from basic_memory.config import DatabaseBackend
from basic_memory.deps.config import AppConfigDep
from basic_memory.deps.db import SessionMakerDep
from basic_memory.models import Entity, MemoryTimeIndex, Project
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.multi_project_search_repository import (
    MultiProjectSearchRepository,
    UnsupportedMultiProjectVectorIndexError,
)
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.schemas.base import normalize_note_type
from basic_memory.schemas.multi_project_search import (
    MultiProjectSearchQuery,
    MultiProjectSearchResponse,
    MultiProjectSearchResult,
)
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode, TemporalResultMetadata
from basic_memory.services.search_service import SearchService

router = APIRouter(tags=["search"])


@router.api_route("/search/", methods=["QUERY"], response_model=MultiProjectSearchResponse)
async def search(
    query: MultiProjectSearchQuery,
    config: AppConfigDep,
    session_maker: SessionMakerDep,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
) -> MultiProjectSearchResponse:
    """Search one tenant database using an explicit, already-authorized project set."""
    response.headers["Accept-Query"] = "application/json"
    response.headers["Cache-Control"] = "no-store"
    exact = query.retrieval_mode == SearchRetrievalMode.FTS
    result = MultiProjectSearchResponse(
        results=[],
        current_page=page,
        page_size=page_size,
        total_is_exact=exact,
        temporal_applied=True if query.has_temporal_filter() else None,
    )
    try:
        prepared = SearchService.prepare_query(query)
        if not query.project_ids or prepared is None:
            return result
        provider = None
        compatible_adapter = (
            config.database_backend == DatabaseBackend.SQLITE
            or config.semantic_vector_index == "pgvector"
        )
        if not exact and compatible_adapter and config.semantic_search_enabled:
            provider = create_embedding_provider(config)
        repository = MultiProjectSearchRepository(
            session_maker,
            query.project_ids,
            app_config=config,
            embedding_provider=provider,
        )
        if prepared.note_types:
            async with db.scoped_session(session_maker) as session:
                stored = await session.scalars(
                    select(Entity.note_type)
                    .where(Entity.project_id.in_(repository.project_ids))
                    .distinct()
                )
                canonical = set(prepared.note_types)
                compatible = canonical | {
                    value for value in stored if value and normalize_note_type(value) in canonical
                }
                prepared = replace(prepared, note_types=sorted(compatible))
        offset = (page - 1) * page_size
        rows = await repository.search(
            prepared, limit=page_size if exact else page_size + 1, offset=offset
        )
        if exact:
            result.total = await repository.count(prepared)
            result.has_more = offset + len(rows) < result.total
        else:
            result.has_more = len(rows) > page_size
            rows = rows[:page_size]
    except UnsupportedMultiProjectVectorIndexError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_multi_project_vector_adapter", "message": str(exc)},
        ) from exc
    except (SemanticDependenciesMissingError, SemanticSearchDisabledError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        return result
    # Batch hydration retains scope even for relation targets outside the visible set.
    # No project service fan-out, and no hidden unrestricted entity lookup.
    entity_ids = {
        entity_id
        for row in rows
        for entity_id in (row.entity_id, row.from_id, row.to_id)
        if entity_id is not None
    }
    temporal: dict[tuple[int, str, int], list[TemporalResultMetadata]] = {}
    async with db.scoped_session(session_maker) as session:
        entities = await session.scalars(
            select(Entity).where(
                Entity.project_id.in_(repository.project_ids), Entity.id.in_(entity_ids)
            )
        )
        entities_by_key = {(entity.project_id, entity.id): entity for entity in entities}
        projects = await session.scalars(
            select(Project).where(Project.id.in_({row.project_id for row in rows}))
        )
        projects_by_id = {project.id: project.external_id for project in projects}
        if query.has_temporal_filter():
            assertions = await session.scalars(
                select(MemoryTimeIndex)
                .where(
                    tuple_(
                        MemoryTimeIndex.project_id,
                        MemoryTimeIndex.source_type,
                        MemoryTimeIndex.source_id,
                    ).in_([(row.project_id, row.type, row.id) for row in rows])
                )
                .order_by(MemoryTimeIndex.id)
            )
            for assertion in assertions:
                key = (assertion.project_id, assertion.source_type, assertion.source_id)
                temporal.setdefault(key, []).append(_temporal_result_metadata(assertion))
    for row in rows:
        owner = (
            entities_by_key.get((row.project_id, row.entity_id))
            if row.entity_id is not None
            else None
        )
        source = (
            entities_by_key.get((row.project_id, row.from_id)) if row.from_id is not None else None
        )
        target = entities_by_key.get((row.project_id, row.to_id)) if row.to_id is not None else None
        result.results.append(
            MultiProjectSearchResult(
                project_id=row.project_id,
                project_external_id=projects_by_id[row.project_id],
                title=row.title or "",
                type=SearchItemType(row.type),
                score=row.score or 0.0,
                permalink=row.permalink,
                entity=owner.permalink if owner else None,
                external_id=owner.external_id if owner else None,
                content=row.content,
                content_length=row.content_length,
                content_truncated=row.content_truncated,
                matched_chunk=row.matched_chunk_text,
                file_path=row.file_path,
                updated_at=row.updated_at,
                metadata=row.metadata,
                entity_id=row.entity_id,
                observation_id=row.id if row.type == SearchItemType.OBSERVATION else None,
                relation_id=row.id if row.type == SearchItemType.RELATION else None,
                category=row.category,
                from_entity=source.permalink if source else None,
                to_entity=target.permalink if target else None,
                relation_type=row.relation_type,
                temporal=temporal.get((row.project_id, row.type, row.id))
                if query.has_temporal_filter()
                else None,
            )
        )
    return result
