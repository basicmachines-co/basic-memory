"""Local note-content materialization adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing import (
    ContentStoreNoteMaterializationFileWriter,
    IndexFileExecutor,
    RepositoryNoteMaterializationPreflight,
    RepositoryNoteMaterializationPublisher,
    RepositoryNoteMaterializationStatusPublisher,
    run_note_file_delete,
    run_note_materialization,
)
from basic_memory.runtime import (
    NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR,
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimeFileChecksum,
    RuntimeFileMetadataSource,
    RuntimeFilePath,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteFileDeleteJobRequest,
    plan_note_file_delete_job_request,
    plan_note_materialization_job_request,
    plan_accepted_note_response,
)
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository, NoteContentRepository
from basic_memory.schemas.response import ObservationResponse, RelationResponse
from basic_memory.services.file_service import FileService

LOCAL_NOTE_CONTENT_TENANT_ID = UUID(int=0)


def note_content_payload_file_path(
    payload: RuntimeNoteContentResponsePayload,
) -> RuntimeFilePath | None:
    """Return the materialized file path carried by an accepted-note payload."""
    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return payload.file_path
    if isinstance(payload, Mapping):
        file_path = payload.get("file_path")
        if isinstance(file_path, str) and file_path:
            return file_path
    return None


def file_write_status_from_materialization_result(
    result: RuntimeNoteMaterializationResult,
) -> str:
    """Return the response write marker for a terminal local materialization result."""
    if result.status is RuntimeNoteMaterializationStatus.conflict:
        return "external_change_detected"
    return "failed"


def note_content_payload_with_materialization_result(
    payload: RuntimeNoteContentResponsePayload,
    result: RuntimeNoteMaterializationResult,
) -> RuntimeNoteContentResponsePayload:
    """Expose a failed local materialization result in the accepted-note response payload."""
    file_write_status = file_write_status_from_materialization_result(result)

    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return replace(
            payload,
            file_write_status=file_write_status,
            file_checksum=result.file_checksum
            if result.file_checksum is not None
            else payload.file_checksum,
            last_materialization_error=result.reason,
        )

    updated_payload = dict(payload)
    updated_payload["file_write_status"] = file_write_status
    updated_payload["last_materialization_error"] = result.reason
    if result.file_checksum is not None:
        updated_payload["file_checksum"] = result.file_checksum
    if file_write_status == "external_change_detected":
        updated_payload["sync_error"] = NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR
    return updated_payload


def indexed_observation_payloads(entity: Entity) -> tuple[dict[str, object], ...]:
    """Serialize loaded observation rows into the v2 response shape."""
    return tuple(
        ObservationResponse.model_validate(observation).model_dump(mode="json")
        for observation in entity.observations
    )


def indexed_relation_payloads(entity: Entity) -> tuple[dict[str, object], ...]:
    """Serialize loaded relation rows into the v2 response shape."""
    return tuple(
        RelationResponse.model_validate(relation).model_dump(mode="json")
        for relation in entity.relations
    )


async def load_indexed_note_content_response_payload(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    entity_id: int,
    fallback_source: str,
) -> RuntimeAcceptedNoteResponse:
    """Reload the local indexed entity graph after inline materialization/indexing."""
    async with db.scoped_session(session_maker) as session:
        entity = await EntityRepository(project_id=project_id).get_by_id(
            session,
            entity_id,
            load_relations=True,
        )
        if entity is None:
            raise RuntimeError(f"Indexed entity {entity_id} was not found after materialization")

        note_content = await NoteContentRepository(project_id=project_id).get_by_entity_id(
            session,
            entity_id,
        )
        if note_content is None:
            raise RuntimeError(
                f"Indexed note_content for entity {entity_id} was not found after materialization"
            )

        return replace(
            plan_accepted_note_response(
                entity=entity,
                note_content=note_content,
                fallback_source=fallback_source,
            ),
            observations=indexed_observation_payloads(entity),
            relations=indexed_relation_payloads(entity),
        )


@dataclass(frozen=True, slots=True)
class LocalNoteContentStorage:
    """Adapt the local FileService to note-content runtime storage protocols."""

    file_service: FileService

    async def write_file(
        self,
        path: RuntimeFilePath,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> RuntimeFileChecksum:
        _ = metadata
        path_obj = self.file_service.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.file_service.base_path / path_obj

        # Accepted-note materialization persists an already-accepted DB snapshot.
        # Writing bytes keeps the materialized file checksum identical to the
        # note_content checksum on Windows, where text mode would translate LF to CRLF.
        await self.file_service.ensure_directory(full_path.parent)
        await file_utils.write_file_atomic_bytes(full_path, content.encode("utf-8"))
        return await self.file_service.compute_checksum(full_path)

    async def get_file_metadata(self, path: RuntimeFilePath) -> RuntimeFileMetadataSource:
        return await self.file_service.get_file_metadata(path)

    async def exists(self, path: RuntimeFilePath) -> bool:
        return await self.file_service.exists(path)

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum:
        return await self.file_service.compute_checksum(path)

    async def delete_file(self, path: RuntimeFilePath) -> None:
        await self.file_service.delete_file(path)


@dataclass(frozen=True, slots=True)
class InlineNoteFileDeleteEnqueuer:
    """Execute note-file cleanup immediately in the local runtime."""

    storage: LocalNoteContentStorage

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        await run_note_file_delete(request, storage=self.storage)


@dataclass(frozen=True, slots=True)
class LocalNoteContentMaterializationProvider:
    """Run accepted-note materialization inline for the local runtime."""

    session_maker: async_sessionmaker[AsyncSession]
    file_service: FileService
    file_indexer: IndexFileExecutor | None = None

    async def materialize_write_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        """Write accepted note content to the local filesystem when needed."""
        if accepted.materialization is None:
            return accepted

        storage = LocalNoteContentStorage(self.file_service)
        cleanup_enqueuer = InlineNoteFileDeleteEnqueuer(storage)
        result = await run_note_materialization(
            plan_note_materialization_job_request(
                tenant_id=LOCAL_NOTE_CONTENT_TENANT_ID,
                materialization=accepted.materialization,
            ),
            preflight=RepositoryNoteMaterializationPreflight(
                session_maker=self.session_maker,
            ),
            writer=ContentStoreNoteMaterializationFileWriter(storage),
            publisher=RepositoryNoteMaterializationPublisher(
                session_maker=self.session_maker,
            ),
            status_publisher=RepositoryNoteMaterializationStatusPublisher(
                session_maker=self.session_maker,
            ),
            cleanup_enqueuer=cleanup_enqueuer,
        )
        if result.status is not RuntimeNoteMaterializationStatus.written:
            return replace(
                accepted,
                payload=note_content_payload_with_materialization_result(
                    accepted.payload,
                    result,
                ),
            )

        file_path = note_content_payload_file_path(accepted.payload)
        if file_path is not None and self.file_indexer is not None:
            await self.file_indexer.index_file(
                file_path,
                source="note-content-materialization",
            )
            return replace(
                accepted,
                payload=await load_indexed_note_content_response_payload(
                    session_maker=self.session_maker,
                    project_id=accepted.materialization.project_id,
                    entity_id=accepted.materialization.entity_id,
                    fallback_source=accepted.materialization.source
                    or "note-content-materialization",
                ),
            )
        return accepted

    async def materialize_delete_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        """Delete materialized files immediately after local accepted-note deletes."""
        if accepted.file_delete is None:
            return accepted

        storage = LocalNoteContentStorage(self.file_service)
        await InlineNoteFileDeleteEnqueuer(storage).enqueue_note_file_delete(
            plan_note_file_delete_job_request(
                tenant_id=LOCAL_NOTE_CONTENT_TENANT_ID,
                file_delete=accepted.file_delete,
            )
        )
        return accepted
