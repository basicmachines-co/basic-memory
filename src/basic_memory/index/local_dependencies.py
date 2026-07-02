"""Local dependency composition for event-based indexing runtimes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.file_utils import FileMetadata, ParseError, compute_checksum, remove_frontmatter
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.embedding_index_planning import EmbeddingIndexBatchSummary
from basic_memory.indexing.file_batch_runner import IndexFileBatchIndexer
from basic_memory.indexing.file_index_checking import (
    IndexedFileChecksumRepository,
    IndexedFileChecksumRow,
)
from basic_memory.indexing.file_indexer import (
    IndexMarkdownEntityRepository,
    IndexMarkdownNoteContentReconciler,
)
from basic_memory.indexing.index_batch_runtime import (
    DefaultIndexBatchRuntime,
    build_default_index_batch_runtime,
)
from basic_memory.indexing.index_file_runner import (
    CurrentMaterializedNoteEntityRepository,
    IndexFileExecutor,
)
from basic_memory.indexing.models import (
    FileIndexOperation,
    FileIndexResult,
    IndexEntitySearchWriter,
    IndexInputFile,
    IndexingBatchResult,
    StorageIndexFileWriter,
    SyncedMarkdownFile,
)
from basic_memory.indexing.orphan_cleanup import OrphanEntityRepository, OrphanSearchIndex
from basic_memory.indexing.relation_resolution import (
    RelationResolutionEntityIndexer,
    RelationResolutionEntityRepository,
    RelationResolutionLinkResolver,
    RelationResolutionRelationRepository,
)
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    note_content_repository_for_project,
)
from basic_memory.markdown import EntityMarkdown, EntityParser, MarkdownProcessor
from basic_memory.models import Entity, Project
from basic_memory.repository import (
    EntityRepository,
    ObservationRepository,
    RelationRepository,
)
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.runtime.storage import ProjectId, RuntimeFilePath
from basic_memory.services import EntityService, FileService
from basic_memory.services.exceptions import FileOperationError
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService


class LocalIndexEntityRepository(
    IndexMarkdownEntityRepository,
    IndexedFileChecksumRepository,
    CurrentMaterializedNoteEntityRepository,
    OrphanEntityRepository[Entity],
    RelationResolutionEntityRepository,
    Protocol,
):
    """Entity repository capabilities needed by local event/project indexing."""

    project_id: ProjectId | None

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: Path | str,
        *,
        load_relations: bool = True,
    ) -> Entity | None: ...

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[Path | str],
    ) -> Sequence[IndexedFileChecksumRow]: ...

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> Sequence[Entity]: ...

    async def find_by_checksums(
        self,
        session: AsyncSession,
        checksums: Sequence[str],
    ) -> Sequence[Entity]: ...

    async def update(
        self,
        session: AsyncSession,
        entity_id: int,
        entity_data: dict[str, object] | Entity,
    ) -> Entity | None: ...

    async def delete_by_fields(
        self,
        session: AsyncSession,
        **filters: object,
    ) -> bool: ...


class LocalIndexSearchService(
    OrphanSearchIndex[Entity],
    RelationResolutionEntityIndexer,
    Protocol,
):
    """Search capabilities needed by local event/project indexing."""

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
    ) -> EmbeddingIndexBatchSummary: ...


class LocalIndexEntityService(Protocol):
    """Entity service capabilities needed by local index maintenance adapters."""

    app_config: BasicMemoryConfig | None

    async def resolve_permalink(
        self,
        file_path: Path | str,
        markdown: EntityMarkdown | None = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class LocalIndexProjectDependencies:
    """Adapter handoff values needed to build local index runtimes."""

    file_service: FileService
    file_indexer: IndexFileExecutor
    file_batch_indexer: IndexFileBatchIndexer[IndexInputFile]
    session_maker: async_sessionmaker[AsyncSession]
    project_id: ProjectId
    entity_repository: LocalIndexEntityRepository
    relation_repository: RelationResolutionRelationRepository
    link_resolver: RelationResolutionLinkResolver
    search_service: LocalIndexSearchService
    entity_service: LocalIndexEntityService


class LocalIndexProjectDependencyProvider(Protocol):
    """Provide local indexing dependencies for a project."""

    async def dependencies_for_project(self, project: Project) -> LocalIndexProjectDependencies:
        """Build local indexing dependencies for one project."""


@dataclass(frozen=True, slots=True)
class DefaultLocalIndexProjectDependencyProvider:
    """Default provider that composes local filesystem index dependencies."""

    async def dependencies_for_project(self, project: Project) -> LocalIndexProjectDependencies:
        return await build_local_index_project_dependencies(project)


@dataclass(frozen=True, slots=True)
class LocalIndexFileBatchIndexer(IndexFileBatchIndexer[IndexInputFile]):
    """Adapt the default loaded-file batch runtime to the file-batch job contract."""

    batch_runtime: DefaultIndexBatchRuntime[IndexInputFile]

    async def index_files(
        self,
        files: Mapping[str, IndexInputFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
        metadata_update_max_concurrent: int | None = None,
        bound_logger: object | None = None,
    ) -> IndexingBatchResult:
        del bound_logger
        return await self.batch_runtime.index_loaded_files(
            files,
            max_concurrent=max_concurrent,
            parse_max_concurrent=parse_max_concurrent,
            metadata_update_max_concurrent=metadata_update_max_concurrent,
        )


@dataclass(frozen=True, slots=True)
class LocalMarkdownFileIndexer(IndexFileExecutor):
    """Index one local markdown file without the legacy sync implementation."""

    file_service: FileService
    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: LocalIndexEntityRepository
    batch_indexer: BatchIndexer
    search_service: IndexEntitySearchWriter
    note_content_reconciler: IndexMarkdownNoteContentReconciler

    async def index_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> FileIndexResult:
        """Read and index the current file with markdown-specific reconciliation when needed."""
        if self.file_service.is_markdown(file_path):
            return await self.index_markdown_file(file_path, source=source)

        return await self.index_regular_file(file_path, source=source)

    async def index_markdown_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> FileIndexResult:
        """Read, persist, search-index, and reconcile one markdown file."""
        logger.info(f"Indexing markdown file: {file_path}")

        async with db.scoped_session(self.session_maker) as session:
            existing = await self.entity_repository.get_by_file_path(
                session,
                file_path,
                load_relations=False,
            )
        operation = FileIndexOperation.created if existing is None else FileIndexOperation.updated

        synced = await self.index_current_markdown_file(
            file_path,
            new=existing is None,
            index_search=True,
            resolve_relations=True,
            refresh_unchanged_derived_state=existing is not None,
        )
        await self.note_content_reconciler.reconcile(
            entity=synced.entity,
            markdown_content=synced.markdown_content,
            observed_at=synced.updated_at,
            source=source,
        )

        logger.info(
            f"Indexed markdown file: {file_path}",
            entity_id=synced.entity.id,
            checksum=synced.checksum,
            operation=operation,
            observation_count=len(synced.entity.observations),
            relation_count=len(synced.entity.relations),
        )
        return FileIndexResult.from_fields(
            file_path=file_path,
            entity_id=synced.entity.id,
            external_id=synced.entity.external_id,
            title=synced.entity.title,
            permalink=synced.entity.permalink,
            checksum=synced.checksum,
            operation=operation,
        )

    async def index_regular_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> FileIndexResult:
        """Read, persist, and search-index one regular file entity."""
        logger.info(f"Indexing regular file: {file_path}", source=source)

        async with db.scoped_session(self.session_maker) as session:
            existing = await self.entity_repository.get_by_file_path(
                session,
                file_path,
                load_relations=False,
            )
        operation = FileIndexOperation.created if existing is None else FileIndexOperation.updated

        file_bytes = await self.file_service.read_file_bytes(file_path)
        file_metadata = await self.file_service.get_file_metadata(file_path)
        checksum = await compute_checksum(file_bytes)
        input_file = IndexInputFile(
            path=file_path,
            size=file_metadata.size,
            checksum=checksum,
            content_type=self.file_service.content_type(file_path),
            last_modified=file_metadata.modified_at,
            created_at=file_metadata.created_at,
            content=file_bytes,
        )
        batch_result = await self.batch_indexer.index_files(
            {file_path: input_file},
            max_concurrent=1,
            parse_max_concurrent=1,
        )
        if batch_result.errors:
            error_path, error_message = batch_result.errors[0]
            raise RuntimeError(f"Regular file indexing failed for {error_path}: {error_message}")
        if len(batch_result.indexed) != 1:
            raise RuntimeError(f"Regular file indexing produced no result for {file_path}")

        indexed = batch_result.indexed[0]
        async with db.scoped_session(self.session_maker) as session:
            refreshed_entities = await self.entity_repository.find_by_ids(
                session,
                [indexed.entity_id],
            )
        if len(refreshed_entities) != 1:  # pragma: no cover
            raise ValueError(f"Failed to reload indexed regular file entity for {file_path}")

        entity = refreshed_entities[0]
        logger.info(
            f"Indexed regular file: {file_path}",
            entity_id=entity.id,
            checksum=indexed.checksum,
            operation=operation,
        )
        return FileIndexResult.from_fields(
            file_path=file_path,
            entity_id=entity.id,
            external_id=entity.external_id,
            title=entity.title,
            permalink=entity.permalink,
            checksum=indexed.checksum,
            operation=operation,
        )

    async def index_current_markdown_file(
        self,
        path: RuntimeFilePath,
        *,
        new: bool,
        index_search: bool,
        resolve_relations: bool,
        refresh_unchanged_derived_state: bool,
    ) -> SyncedMarkdownFile:
        """Index the current local markdown bytes and return canonical file state."""
        logger.debug(f"Parsing markdown file, path: {path}, new: {new}")

        try:
            initial_markdown_bytes = await self.file_service.read_file_bytes(path)
        except FileOperationError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                raise exc.__cause__ from exc
            raise

        initial_markdown_content = initial_markdown_bytes.decode("utf-8")
        file_metadata = await self.file_service.get_file_metadata(path)
        initial_checksum = await compute_checksum(initial_markdown_bytes)

        async with db.scoped_session(self.session_maker) as session:
            existing_entity = await self.entity_repository.get_by_file_path(session, path)

        input_file = IndexInputFile(
            path=path,
            size=file_metadata.size,
            checksum=initial_checksum,
            content_type=self.file_service.content_type(path),
            last_modified=file_metadata.modified_at,
            created_at=file_metadata.created_at,
            content=initial_markdown_bytes,
        )
        if existing_entity is not None and existing_entity.checksum == initial_checksum:
            if refresh_unchanged_derived_state:
                return await self.refresh_unchanged_markdown_file(
                    input_file,
                    existing_entity=existing_entity,
                    initial_markdown_content=initial_markdown_content,
                    file_metadata=file_metadata,
                    index_search=index_search,
                    resolve_relations=resolve_relations,
                )

            logger.debug(
                f"Markdown index skipped unchanged file: path={path}, "
                f"entity_id={existing_entity.id}, checksum={initial_checksum[:8]}"
            )
            return SyncedMarkdownFile(
                entity=existing_entity,
                checksum=initial_checksum,
                markdown_content=initial_markdown_content,
                file_path=path,
                content_type=self.file_service.content_type(path),
                updated_at=file_metadata.modified_at,
                size=file_metadata.size,
            )

        return await self.index_changed_markdown_file(
            input_file,
            initial_markdown_content=initial_markdown_content,
            new=new,
            index_search=index_search,
            resolve_relations=resolve_relations,
        )

    async def refresh_unchanged_markdown_file(
        self,
        input_file: IndexInputFile,
        *,
        existing_entity: Entity,
        initial_markdown_content: str,
        file_metadata: FileMetadata,
        index_search: bool,
        resolve_relations: bool,
    ) -> SyncedMarkdownFile:
        """Refresh derived DB/search state for a markdown file with unchanged bytes."""
        logger.debug(
            f"Markdown index refreshing unchanged derived state: path={input_file.path}, "
            f"entity_id={existing_entity.id}, checksum={input_file.checksum[:8] if input_file.checksum else None}"
        )
        indexed = await self.batch_indexer.index_markdown_file(
            input_file,
            new=False,
            index_search=index_search,
            resolve_relations=resolve_relations,
        )
        async with db.scoped_session(self.session_maker) as session:
            refreshed_entities = await self.entity_repository.find_by_ids(
                session,
                [indexed.entity_id],
            )
        if len(refreshed_entities) != 1:  # pragma: no cover
            raise ValueError(f"Failed to reload refreshed markdown entity for {input_file.path}")
        return SyncedMarkdownFile(
            entity=refreshed_entities[0],
            checksum=indexed.checksum,
            markdown_content=indexed.markdown_content or initial_markdown_content,
            file_path=input_file.path,
            content_type=self.file_service.content_type(input_file.path),
            updated_at=file_metadata.modified_at,
            size=file_metadata.size,
        )

    async def index_changed_markdown_file(
        self,
        input_file: IndexInputFile,
        *,
        initial_markdown_content: str,
        new: bool,
        index_search: bool,
        resolve_relations: bool,
    ) -> SyncedMarkdownFile:
        """Persist changed markdown content and refresh derived index state."""
        indexed = await self.batch_indexer.index_markdown_file(
            input_file,
            new=new,
            index_search=False,
            resolve_relations=resolve_relations,
        )
        final_markdown_content = indexed.markdown_content or initial_markdown_content
        file_metadata = await self.file_service.get_file_metadata(input_file.path)
        async with db.scoped_session(self.session_maker) as session:
            refreshed_entities = await self.entity_repository.find_by_ids(
                session,
                [indexed.entity_id],
            )
            if len(refreshed_entities) != 1:  # pragma: no cover
                raise ValueError(f"Failed to reload synced markdown entity for {input_file.path}")
            updated_entity = await self.entity_repository.update(
                session,
                refreshed_entities[0].id,
                {
                    "checksum": indexed.checksum,
                    "created_at": file_metadata.created_at,
                    "updated_at": file_metadata.modified_at,
                    "mtime": file_metadata.modified_at.timestamp(),
                    "size": file_metadata.size,
                },
            )
        if updated_entity is None:  # pragma: no cover
            raise ValueError(f"Failed to update markdown entity metadata for {input_file.path}")

        if index_search:
            try:
                search_content = remove_frontmatter(final_markdown_content)
            except ParseError:
                search_content = final_markdown_content
            await self.search_service.index_entity_data(
                updated_entity,
                content=search_content,
            )

        logger.debug(
            f"Markdown index completed: path={input_file.path}, entity_id={updated_entity.id}, "
            f"observation_count={len(updated_entity.observations)}, "
            f"relation_count={len(updated_entity.relations)}, checksum={indexed.checksum[:8]}"
        )
        return SyncedMarkdownFile(
            entity=updated_entity,
            checksum=indexed.checksum,
            markdown_content=final_markdown_content,
            file_path=input_file.path,
            content_type=self.file_service.content_type(input_file.path),
            updated_at=file_metadata.modified_at,
            size=file_metadata.size,
        )


def build_local_markdown_file_indexer(
    *,
    project_id: ProjectId,
    file_service: FileService,
    session_maker: async_sessionmaker[AsyncSession],
    entity_repository: LocalIndexEntityRepository,
    batch_indexer: BatchIndexer,
    search_service: IndexEntitySearchWriter,
) -> LocalMarkdownFileIndexer:
    """Compose the default local markdown file indexer without legacy sync."""
    return LocalMarkdownFileIndexer(
        file_service=file_service,
        session_maker=session_maker,
        entity_repository=entity_repository,
        batch_indexer=batch_indexer,
        search_service=search_service,
        note_content_reconciler=NoteContentReconciler(
            note_content_repository=note_content_repository_for_project(project_id),
            session_maker=session_maker,
        ),
    )


async def build_local_index_project_dependencies(
    project: Project,
) -> LocalIndexProjectDependencies:
    """Build local project dependencies for event/project indexing."""
    app_config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )

    project_path = Path(project.path)
    entity_parser = EntityParser(project_path)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(project_path, markdown_processor, app_config=app_config)

    entity_repository = EntityRepository(project_id=project.id)
    observation_repository = ObservationRepository(project_id=project.id)
    relation_repository = RelationRepository(project_id=project.id)
    search_repository = create_search_repository(
        session_maker,
        project_id=project.id,
        app_config=app_config,
    )
    search_service = SearchService(
        search_repository,
        entity_repository,
        file_service,
        session_maker,
    )
    link_resolver = LinkResolver(entity_repository, search_service, session_maker)
    entity_service = EntityService(
        entity_parser,
        entity_repository,
        observation_repository,
        relation_repository,
        file_service,
        link_resolver,
        session_maker,
        search_service=search_service,
        app_config=app_config,
    )
    batch_indexer = BatchIndexer(
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_writer=StorageIndexFileWriter(storage=file_service),
        session_maker=session_maker,
    )
    batch_runtime = build_default_index_batch_runtime(
        project_id=project.id,
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_writer=search_service,
        frontmatter_storage=file_service,
        content_type_provider=file_service,
        session_maker=session_maker,
    )
    file_indexer = build_local_markdown_file_indexer(
        project_id=project.id,
        file_service=file_service,
        session_maker=session_maker,
        entity_repository=entity_repository,
        batch_indexer=batch_indexer,
        search_service=search_service,
    )
    return LocalIndexProjectDependencies(
        file_service=file_service,
        file_indexer=file_indexer,
        file_batch_indexer=LocalIndexFileBatchIndexer(batch_runtime),
        session_maker=session_maker,
        project_id=project.id,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        link_resolver=link_resolver,
        search_service=search_service,
        entity_service=entity_service,
    )
