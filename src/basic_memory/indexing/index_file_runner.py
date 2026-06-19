"""Portable orchestration for one file-index job."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.file_indexer import IndexMarkdownEntity
from basic_memory.indexing.file_index_planning import (
    FileIndexDecisionStatus,
    FileIndexPlan,
    FileIndexTarget,
)
from basic_memory.indexing.index_file_runtime import IndexFileRuntimeRequest
from basic_memory.indexing.models import (
    CurrentMaterializedNoteEntity,
    FileIndexResult,
    IndexFileJobResult,
    IndexFileJobStatus,
    index_file_job_result_from_indexed_file,
    plan_current_materialized_note_result,
    plan_indexed_file_live_update_metadata,
)
from basic_memory.runtime import (
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteObjectMetadataMap,
    RuntimeStorageFileIndexMode,
)
from basic_memory.services.exceptions import FileOperationError


@dataclass(frozen=True, slots=True)
class IndexFileObjectMetadata:
    """Current storage metadata needed by one file-index job."""

    checksum: RuntimeFileChecksum
    metadata: RuntimeNoteObjectMetadataMap = field(default_factory=dict)


class IndexFileRunnerChecker(Protocol):
    """Capability that decides whether an observed object needs indexing."""

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan: ...


class IndexFileMetadataSource(Protocol):
    """Capability that loads current storage metadata for one file path."""

    async def load_current_file_metadata(
        self,
        file_path: RuntimeFilePath,
    ) -> IndexFileObjectMetadata | None: ...


class IndexFileMaterializedNoteSource(Protocol):
    """Capability that loads accepted entity state for materialized-note checks."""

    async def load_current_materialized_note_entity(
        self,
        file_path: RuntimeFilePath,
    ) -> CurrentMaterializedNoteEntity | None: ...


class IndexFileExecutor(Protocol):
    """Capability that indexes one markdown file from current storage."""

    async def index_markdown_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> FileIndexResult: ...


class CurrentMaterializedNoteEntityRepository(Protocol):
    """Repository capability needed to load the current materialized note entity."""

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: RuntimeFilePath,
        *,
        load_relations: bool = True,
    ) -> IndexMarkdownEntity | None: ...


@dataclass(frozen=True, slots=True)
class RepositoryCurrentMaterializedNoteSource:
    """Load accepted note identity from a repository using caller-owned sessions."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: CurrentMaterializedNoteEntityRepository

    async def load_current_materialized_note_entity(
        self,
        file_path: RuntimeFilePath,
    ) -> CurrentMaterializedNoteEntity | None:
        async with db.scoped_session(self.session_maker) as session:
            entity = await self.entity_repository.get_by_file_path(
                session,
                file_path,
                load_relations=False,
            )
        if entity is None:
            return None

        return CurrentMaterializedNoteEntity.from_fields(
            entity_id=int(entity.id),
            external_id=entity.external_id,
            title=entity.title,
            permalink=entity.permalink,
            checksum=entity.checksum,
            file_path=file_path,
        )


async def run_index_file(
    request: IndexFileRuntimeRequest,
    *,
    checker: IndexFileRunnerChecker | None = None,
    metadata_source: IndexFileMetadataSource,
    materialized_note_source: IndexFileMaterializedNoteSource,
    file_indexer: IndexFileExecutor,
) -> IndexFileJobResult:
    """Run one storage-neutral file-index request."""
    if request.object_observation is not None:
        if checker is None:
            raise RuntimeError("Observed-object index_file requests require a metadata checker")
        terminal_result = await require_observed_file_should_read(
            request,
            checker=checker,
            metadata_source=metadata_source,
            materialized_note_source=materialized_note_source,
        )
        if terminal_result is not None:
            return terminal_result
    else:
        current_metadata = await metadata_source.load_current_file_metadata(request.file_path)
        if current_metadata is None:
            return IndexFileJobResult(
                status=IndexFileJobStatus.missing,
                reason=f"file not found: {request.file_path}",
            )

    source = index_file_source_for_mode(request.mode)
    try:
        indexed_file = await file_indexer.index_markdown_file(
            request.file_path,
            source=source,
        )
    except FileOperationError:
        current_metadata = await metadata_source.load_current_file_metadata(request.file_path)
        if current_metadata is None:
            return IndexFileJobResult(
                status=IndexFileJobStatus.missing,
                reason=f"file deleted before indexing: {request.file_path}",
            )
        raise

    live_update_plan = None
    if request.mode == RuntimeStorageFileIndexMode.observed_object:
        try:
            current_metadata = await metadata_source.load_current_file_metadata(request.file_path)
        except FileOperationError:
            current_metadata = None

        if current_metadata is not None:
            live_update_plan = plan_indexed_file_live_update_metadata(
                indexed_file=indexed_file,
                object_checksum=current_metadata.checksum,
                object_metadata=current_metadata.metadata,
            )

    return index_file_job_result_from_indexed_file(
        indexed_file,
        live_update_plan=live_update_plan,
    )


async def require_observed_file_should_read(
    request: IndexFileRuntimeRequest,
    *,
    checker: IndexFileRunnerChecker,
    metadata_source: IndexFileMetadataSource,
    materialized_note_source: IndexFileMaterializedNoteSource,
) -> None | IndexFileJobResult:
    """Return a terminal result when observed metadata proves no content read is needed."""
    if request.object_observation is None:
        return None

    plan = await checker.detect(
        [
            FileIndexTarget.from_observed_storage_object(
                path=request.file_path,
                etag=request.object_observation.etag,
                size=request.object_observation.size,
            )
        ]
    )
    if plan.paths_to_read:
        if plan.paths_to_read != (request.file_path,):
            raise RuntimeError("index_file metadata check returned an unexpected path")
        return None

    if len(plan.decisions) != 1:
        raise RuntimeError("index_file metadata check returned no decision")

    decision = plan.decisions[0]
    if decision.status == FileIndexDecisionStatus.current:
        return await build_current_materialized_note_result(
            request,
            reason=decision.reason,
            metadata_source=metadata_source,
            materialized_note_source=materialized_note_source,
        )
    if decision.status == FileIndexDecisionStatus.missing:
        return IndexFileJobResult(
            status=IndexFileJobStatus.missing,
            reason=decision.reason,
        )
    raise RuntimeError(f"Unexpected file index decision: {decision.status}")


async def build_current_materialized_note_result(
    request: IndexFileRuntimeRequest,
    *,
    reason: str,
    metadata_source: IndexFileMetadataSource,
    materialized_note_source: IndexFileMaterializedNoteSource,
) -> IndexFileJobResult:
    """Build a current-file result that preserves DB-first note provenance."""
    current_result = IndexFileJobResult(status=IndexFileJobStatus.current, reason=reason)
    if request.mode != RuntimeStorageFileIndexMode.observed_object:
        return current_result

    try:
        current_metadata = await metadata_source.load_current_file_metadata(request.file_path)
    except FileOperationError:
        return current_result

    if current_metadata is None:
        return current_result

    initial_plan = plan_current_materialized_note_result(
        reason=reason,
        file_path=request.file_path,
        object_checksum=current_metadata.checksum,
        object_metadata=current_metadata.metadata,
        entity=None,
    )
    if not initial_plan.requires_entity:
        return initial_plan.job_result

    entity = await materialized_note_source.load_current_materialized_note_entity(request.file_path)
    if entity is None:
        return initial_plan.job_result

    plan = plan_current_materialized_note_result(
        reason=reason,
        file_path=request.file_path,
        object_checksum=current_metadata.checksum,
        object_metadata=current_metadata.metadata,
        entity=entity,
    )
    return plan.job_result


def index_file_source_for_mode(mode: RuntimeStorageFileIndexMode) -> str:
    """Return the legacy indexing source label for a file-index mode."""
    if mode == RuntimeStorageFileIndexMode.observed_object:
        return "s3_webhook"
    return "project_index"
