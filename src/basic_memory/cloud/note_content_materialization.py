"""Local note-content materialization adapters."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing import (
    ContentStoreNoteMaterializationFileWriter,
    RepositoryNoteMaterializationPreflight,
    RepositoryNoteMaterializationPublisher,
    RepositoryNoteMaterializationStatusPublisher,
    run_note_file_delete,
    run_note_materialization,
)
from basic_memory.runtime import (
    RuntimeAcceptedNoteChange,
    RuntimeFileChecksum,
    RuntimeFileMetadataSource,
    RuntimeFilePath,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteFileDeleteJobRequest,
    plan_note_file_delete_job_request,
    plan_note_materialization_job_request,
)
from basic_memory.services.file_service import FileService

LOCAL_NOTE_CONTENT_TENANT_ID = UUID(int=0)


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
        return await self.file_service.write_file(path, content)

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

    async def materialize_write_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> None:
        """Write accepted note content to the local filesystem when needed."""
        if accepted.materialization is None:
            return

        storage = LocalNoteContentStorage(self.file_service)
        cleanup_enqueuer = InlineNoteFileDeleteEnqueuer(storage)
        await run_note_materialization(
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

    async def materialize_delete_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> None:
        """Delete materialized files immediately after local accepted-note deletes."""
        if accepted.file_delete is None:
            return

        storage = LocalNoteContentStorage(self.file_service)
        await InlineNoteFileDeleteEnqueuer(storage).enqueue_note_file_delete(
            plan_note_file_delete_job_request(
                tenant_id=LOCAL_NOTE_CONTENT_TENANT_ID,
                file_delete=accepted.file_delete,
            )
        )
