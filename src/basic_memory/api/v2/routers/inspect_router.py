"""V2 router for read-only retrieval inspection."""

from typing import assert_never

from fastapi import APIRouter, HTTPException

from basic_memory.deps import (
    FileServiceV2ExternalDep,
    LinkResolverV2ExternalDep,
    ProjectExternalIdPathDep,
    SearchRepositoryV2ExternalDep,
)
from basic_memory.schemas.inspect import (
    InspectChunk,
    InspectChunkReadiness,
    InspectChunksRequest,
    InspectChunksResponse,
    InspectDetachedSearchRow,
    InspectIndexBehindRowsDetail,
    InspectRowsBehindFileDetail,
    InspectSearchRow,
)
from basic_memory.services.retrieval_inspect import (
    ChunkFresh,
    ChunkFreshnessUnknown,
    ChunkNotIndexed,
    ChunkIndexBehindRows,
    ChunkRowsBehindFile,
    inspect_entity_chunks,
)

router = APIRouter(prefix="/inspect", tags=["inspect"])


@router.post("/chunks", response_model=InspectChunksResponse)
async def inspect_chunks(
    data: InspectChunksRequest,
    project_id: ProjectExternalIdPathDep,
    link_resolver: LinkResolverV2ExternalDep,
    search_repository: SearchRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> InspectChunksResponse:
    """Show how one note is represented by search rows and vector chunks."""
    entity = await link_resolver.resolve_entity(
        data.identifier,
        load_relations=False,
    )
    if entity is None or entity.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"Entity not found: '{data.identifier}'")

    inspection = await inspect_entity_chunks(search_repository, entity, file_service)
    freshness_detail: InspectIndexBehindRowsDetail | InspectRowsBehindFileDetail | None
    match inspection.freshness:
        case ChunkFresh() | ChunkNotIndexed():
            freshness_detail = None
        case ChunkIndexBehindRows():
            freshness_detail = InspectIndexBehindRowsDetail(
                entity_fingerprint_indexed=(
                    list(inspection.freshness.entity_fingerprint_indexed)
                    if isinstance(
                        inspection.freshness.entity_fingerprint_indexed,
                        tuple,
                    )
                    else inspection.freshness.entity_fingerprint_indexed
                ),
                entity_fingerprint_current=(inspection.freshness.entity_fingerprint_current),
                missing_chunk_count=inspection.freshness.missing_chunk_count,
            )
        case ChunkRowsBehindFile() | ChunkFreshnessUnknown():
            evidence = inspection.freshness.evidence
            freshness_detail = InspectRowsBehindFileDetail(
                entity_checksum=evidence.entity_checksum,
                current_file_checksum=evidence.current_file_checksum,
                db_checksum=evidence.db_checksum,
                file_checksum=evidence.file_checksum,
                file_write_status=evidence.file_write_status,
            )
        case unexpected:  # pragma: no cover - EntityChunkFreshness is exhaustive
            assert_never(unexpected)
    return InspectChunksResponse(
        entity_id=entity.id,
        external_id=entity.external_id,
        permalink=entity.permalink,
        file_path=entity.file_path,
        title=entity.title,
        entity_checksum=entity.checksum,
        configured_embedding_model=inspection.configured_identity.embedding_model,
        configured_vector_index=inspection.configured_identity.vector_index,
        readiness=InspectChunkReadiness(
            total=inspection.readiness.total,
            ready=inspection.readiness.ready,
            pending=inspection.readiness.pending,
            stale=inspection.readiness.stale,
            orphaned=inspection.readiness.orphaned,
            missing=inspection.readiness.missing,
        ),
        entity_fingerprint_indexed=(
            list(inspection.entity_fingerprint_indexed)
            if isinstance(inspection.entity_fingerprint_indexed, tuple)
            else inspection.entity_fingerprint_indexed
        ),
        entity_fingerprint_current=inspection.entity_fingerprint_current,
        stale=inspection.stale,
        freshness=inspection.freshness.value,
        freshness_detail=freshness_detail,
        rows=[
            InspectSearchRow(
                type=inspected_row.search_row.type,
                id=inspected_row.search_row.id,
                title=inspected_row.search_row.title,
                category=inspected_row.search_row.category,
                relation_type=inspected_row.search_row.relation_type,
                content_preview=inspected_row.search_row.content_snippet,
                chunks=[
                    InspectChunk(
                        chunk_key=chunk.stored_row.chunk_key,
                        ordinal=chunk.ordinal,
                        text=chunk.stored_row.chunk_text,
                        source_hash=chunk.stored_row.source_hash,
                        embedding_model=chunk.stored_row.embedding_model,
                        vector_index=chunk.stored_row.vector_index,
                        status=chunk.status,
                        updated_at=chunk.stored_row.updated_at,
                    )
                    for chunk in inspected_row.chunks
                ],
            )
            for inspected_row in inspection.rows
        ],
        detached=[
            InspectDetachedSearchRow(
                type=detached_row.row_type,
                id=detached_row.row_id,
                chunks=[
                    InspectChunk(
                        chunk_key=chunk.stored_row.chunk_key,
                        ordinal=chunk.ordinal,
                        text=chunk.stored_row.chunk_text,
                        source_hash=chunk.stored_row.source_hash,
                        embedding_model=chunk.stored_row.embedding_model,
                        vector_index=chunk.stored_row.vector_index,
                        status=chunk.status,
                        updated_at=chunk.stored_row.updated_at,
                    )
                    for chunk in detached_row.chunks
                ],
            )
            for detached_row in inspection.detached
        ],
    )
