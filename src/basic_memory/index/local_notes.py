"""Local runtime implementations for accepted-note mutations and note-file cleanup.

The accepted-note mutation runner, note-content mutation service, and
directory-delete runtime are storage-neutral; this module supplies their
filesystem-backed local implementations. The FastAPI composition root in
``basic_memory.deps.services`` wires these into route dependencies; cloud
composes its own tenant-scoped equivalents behind the same protocols.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import FileError
from basic_memory.indexing.accepted_note_mutation_runner import AcceptedNoteMutationPreparer
from basic_memory.indexing.directory_delete_runner import DirectoryFileDeleteEnqueueError
from basic_memory.indexing.note_file_delete_runner import run_note_file_delete
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Project
from basic_memory.repository import NoteContentRepository, ObservationRepository, RelationRepository
from basic_memory.repository.accepted_note_search_repository import AcceptedNoteSearchRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.runtime.cleanup import RuntimeFileDeleteResult, RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.storage import (
    ProjectId,
    RuntimeFileChecksum,
    RuntimeFilePath,
    runtime_content_type_is_markdown,
)
from basic_memory.services import EntityService
from basic_memory.services.exceptions import FileOperationError
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService

# --- Accepted-Note Mutations ---


@dataclass(frozen=True, slots=True)
class LocalAcceptedNotePreparerFactory:
    """Construct prepare-only note semantics for local accepted-note mutations."""

    session_maker: async_sessionmaker[AsyncSession]
    app_config: BasicMemoryConfig

    def create_note_preparer(self, project: Project) -> AcceptedNoteMutationPreparer:
        entity_parser = EntityParser(Path(project.path))
        markdown_processor = MarkdownProcessor(entity_parser, app_config=self.app_config)
        file_service = FileService(
            Path(project.path),
            markdown_processor,
            app_config=self.app_config,
        )
        entity_repository = EntityRepository(project_id=project.id)
        search_repository = create_search_repository(
            self.session_maker,
            project_id=project.id,
            app_config=self.app_config,
        )
        search_service = SearchService(
            search_repository,
            entity_repository,
            file_service,
            self.session_maker,
        )
        link_resolver = LinkResolver(
            entity_repository=entity_repository,
            search_service=search_service,
            session_maker=self.session_maker,
        )
        return EntityService(
            entity_repository=entity_repository,
            observation_repository=ObservationRepository(project_id=project.id),
            relation_repository=RelationRepository(project_id=project.id),
            entity_parser=entity_parser,
            file_service=file_service,
            link_resolver=link_resolver,
            session_maker=self.session_maker,
            search_service=search_service,
            app_config=self.app_config,
        )


@dataclass(frozen=True, slots=True)
class LocalAcceptedNoteRepositories:
    """Project-scoped core repositories for accepted-note mutations.

    One concrete bundle satisfies both the lookup and write repository
    capabilities the accepted-note mutation runner consumes; cloud composes its
    own tenant-scoped equivalent behind the same protocols.
    """

    def entity_repository(self, project_id: ProjectId) -> EntityRepository:
        return EntityRepository(project_id=project_id)

    def pending_entity_repository(self, project_id: ProjectId) -> EntityRepository:
        return EntityRepository(project_id=project_id)

    def note_content_repository(self, project_id: ProjectId) -> NoteContentRepository:
        return NoteContentRepository(project_id=project_id)

    def search_repository(self, project_id: ProjectId) -> AcceptedNoteSearchRepository:
        return AcceptedNoteSearchRepository(project_id=project_id)

    def observation_repository(self, project_id: ProjectId) -> ObservationRepository:
        return ObservationRepository(project_id=project_id)

    def relation_repository(self, project_id: ProjectId) -> RelationRepository:
        return RelationRepository(project_id=project_id)


# --- Current-Note Content Freshening ---


class LocalCurrentNoteEntity(Protocol):
    """Entity fields needed to refresh current markdown before route mutation."""

    file_path: str
    content_type: str


class LocalCurrentNoteEntityRepository(Protocol):
    """Entity lookup needed by the local note-content freshener."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
        *,
        load_relations: bool = True,
    ) -> LocalCurrentNoteEntity | None: ...


class LocalCurrentNoteFileService(Protocol):
    """Current file-state access needed before mutating accepted note content."""

    async def exists(
        self,
        path: RuntimeFilePath,
    ) -> bool: ...


class LocalCurrentNoteFileIndexer(Protocol):
    """Single-file indexing capability used by the local note-content freshener."""

    async def index_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class LocalCurrentNoteContentFreshener:
    """Converge directly-edited local markdown before accepted-note mutations."""

    entity_repository: LocalCurrentNoteEntityRepository
    file_service: LocalCurrentNoteFileService
    file_indexer: LocalCurrentNoteFileIndexer
    session_maker: async_sessionmaker[AsyncSession]

    async def freshen_note_content(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> None:
        del project_external_id

        async with self.session_maker() as session:
            entity = await self.entity_repository.get_by_external_id(
                session,
                entity_external_id,
                load_relations=False,
            )
            if entity is None or not runtime_content_type_is_markdown(entity):
                return
            file_path = entity.file_path

        if not await self.file_service.exists(file_path):
            return

        await self.file_indexer.index_file(
            file_path,
            source="note-content-mutation-freshen",
        )


# --- Directory-Delete File Cleanup ---


@dataclass(frozen=True, slots=True)
class LocalNoteFileDeleteStorage:
    """Adapt local FileService to guarded materialized-note cleanup."""

    file_service: FileService

    async def exists(self, path: RuntimeFilePath) -> bool:
        return await self.file_service.exists(path)

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum:
        return await self.file_service.compute_checksum(path)

    async def delete_file(self, path: RuntimeFilePath) -> None:
        await self.file_service.delete_file(path)


@dataclass(frozen=True, slots=True)
class LocalDirectoryFileDeleteEnqueuer:
    """Run accepted directory-delete file cleanup inline for the local runtime."""

    file_service: FileService

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult | None:
        # Local cleanup runs inline, so return the guarded delete result; a skipped
        # delete (file changed before cleanup) must not be reported as a success.
        try:
            return await run_note_file_delete(
                request,
                storage=LocalNoteFileDeleteStorage(file_service=self.file_service),
            )
        except (FileError, FileOperationError, OSError) as exc:
            raise DirectoryFileDeleteEnqueueError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class LocalDirectoryDeleteRelationCleanupRefresher:
    """Reindex surviving relation sources after an accepted directory delete.

    Their search_index relation rows went stale when the deleted targets'
    rows cascaded away; reindexing each surviving source drops the danglers.
    """

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: EntityRepository
    search_service: SearchService

    async def refresh_relation_sources(self, entity_ids: Sequence[int]) -> None:
        unique_entity_ids = sorted(set(entity_ids))
        if not unique_entity_ids:
            return

        async with db.scoped_session(self.session_maker) as session:
            entities = await self.entity_repository.find_by_ids(session, unique_entity_ids)

        # A source deleted between acceptance and this refresh has no search rows
        # left to repair, so missing ids are skipped rather than treated as fatal.
        for entity in entities:
            await self.search_service.index_entity(entity)
