"""Reusable batch executor for bounded-parallel file indexing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence, TypeVar

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import logfire
from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import (
    ParseError,
    compute_checksum,
    has_frontmatter,
    remove_frontmatter,
)
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.indexing.models import (
    IndexEntitySearchWriter,
    IndexedEntity,
    IndexedObservation,
    IndexedRelation,
    IndexFileWriter,
    IndexFrontmatterUpdate,
    IndexingBatchResult,
    IndexInputFile,
    RelationGenerationBatchResult,
)
from basic_memory.indexing.relation_resolution import (
    RelationSearchRefreshResult,
    RepositoryRelationResolutionRuntime,
)
from basic_memory.indexing.relation_persistence import RelationGenerationPublisher
from basic_memory.models import Entity, NoteContent, Relation, RelationSearchRefresh
from basic_memory.repository import EntityRepository, ObservationRepository, RelationRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.relation_repository import lock_note_content_before_entity_mutation
from basic_memory.runtime.storage import (
    ProjectId,
    RUNTIME_MARKDOWN_CONTENT_TYPE,
    runtime_file_path_is_markdown_note,
)
from basic_memory.services import EntityService
from basic_memory.services.bulk_link_resolver import BulkLinkResolver
from basic_memory.services.exceptions import SyncFatalError

T = TypeVar("T")
RUNTIME_RESOURCE_CONTENT_TYPE = "application/octet-stream"


def regular_file_content_type(file: IndexInputFile) -> str:
    """Return a persisted MIME type that cannot reclassify a resource as a note."""
    if (
        file.content_type == RUNTIME_MARKDOWN_CONTENT_TYPE
        and not runtime_file_path_is_markdown_note(Path(file.path).as_posix())
    ):
        return RUNTIME_RESOURCE_CONTENT_TYPE
    return file.content_type or "text/plain"


@dataclass(frozen=True, slots=True)
class MarkdownOnlyIndexEntitySearchWriter:
    """Filter regular file entities out of batch search indexing."""

    search_writer: IndexEntitySearchWriter

    async def index_entity_data(self, entity: Entity, content: str | None = None) -> None:
        if not entity.is_markdown:
            return

        await self.search_writer.index_entity_data(entity, content=content)


@dataclass(frozen=True, slots=True)
class RelationResolutionSearchWriter:
    """Adapt the portable single-entity writer to resolver batch refreshes."""

    search_writer: IndexEntitySearchWriter

    async def index_entities(
        self,
        entities: Sequence[Entity],
        *,
        content_by_entity_id: Mapping[int, str],
    ) -> RelationSearchRefreshResult:
        missing_content_entity_ids: set[int] = set()
        for entity in sorted(entities, key=lambda item: item.id):
            try:
                await self.search_writer.index_entity_data(
                    entity,
                    content=content_by_entity_id.get(entity.id),
                )
            except FileNotFoundError:
                missing_content_entity_ids.add(entity.id)
        return RelationSearchRefreshResult(
            missing_content_entity_ids=frozenset(missing_content_entity_ids)
        )


@dataclass(slots=True)
class _PreparedMarkdownFile:
    file: IndexInputFile
    content: str
    final_checksum: str
    markdown: EntityMarkdown
    file_contains_frontmatter: bool


@dataclass(slots=True)
class _PreparedEntity:
    path: str
    entity_id: int
    permalink: str | None
    checksum: str
    content_type: str | None
    search_content: str | None
    markdown_content: str | None = None
    observations: tuple[IndexedObservation, ...] = ()
    relations: tuple[IndexedRelation, ...] = ()
    resolve_relations: bool = True
    refresh_search: bool = True


@dataclass(slots=True)
class _PersistedMarkdownFile:
    prepared: _PreparedMarkdownFile
    entity: Entity


class BatchIndexer:
    """Index already-loaded files without assuming where they came from."""

    def __init__(
        self,
        *,
        project_id: ProjectId,
        app_config: BasicMemoryConfig,
        entity_service: EntityService,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        search_service: IndexEntitySearchWriter,
        file_writer: IndexFileWriter,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self.app_config = app_config
        self.entity_service = entity_service
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.note_content_repository = NoteContentRepository(project_id=project_id)
        self.search_service = search_service
        self.file_writer = file_writer
        self.session_maker = session_maker
        self.relation_generation_publisher = RelationGenerationPublisher(
            relation_repository=relation_repository,
            observation_repository=observation_repository,
            session_maker=session_maker,
        )
        self.relation_resolution = RepositoryRelationResolutionRuntime(
            session_maker=session_maker,
            relation_repository=relation_repository,
            entity_repository=entity_repository,
            note_content_repository=self.note_content_repository,
            target_resolver=BulkLinkResolver(
                entity_repository=entity_repository,
                app_config=app_config,
            ),
            entity_indexer=RelationResolutionSearchWriter(search_service),
        )

    async def index_files(
        self,
        files: Mapping[str, IndexInputFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
        metadata_update_max_concurrent: int | None = None,
        existing_permalink_by_path: dict[str, str | None] | None = None,
    ) -> IndexingBatchResult:
        """Index one batch of loaded files with bounded concurrency."""
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be greater than zero")
        if metadata_update_max_concurrent is not None and metadata_update_max_concurrent <= 0:
            raise ValueError("metadata_update_max_concurrent must be greater than zero")

        ordered_paths = sorted(files)
        if not ordered_paths:
            return IndexingBatchResult()

        parse_limit = parse_max_concurrent or max_concurrent
        error_by_path: dict[str, str] = {}

        markdown_paths = [path for path in ordered_paths if self._is_markdown(files[path])]
        regular_paths = [path for path in ordered_paths if path not in markdown_paths]

        prepared_markdown, parse_errors = await self._run_bounded(
            markdown_paths,
            limit=parse_limit,
            worker=lambda path: self._prepare_markdown_file(files[path]),
        )
        error_by_path.update(parse_errors)

        prepared_markdown, normalization_errors = await self._normalize_markdown_batch(
            prepared_markdown,
            existing_permalink_by_path=existing_permalink_by_path,
        )
        error_by_path.update(normalization_errors)

        indexed_entities: list[IndexedEntity] = []
        search_indexed = 0

        prepared_entities: dict[str, _PreparedEntity] = {}

        markdown_upserts, markdown_errors = await self._run_bounded(
            [path for path in markdown_paths if path not in error_by_path],
            limit=max_concurrent,
            worker=lambda path: self._upsert_markdown_file(prepared_markdown[path]),
        )
        error_by_path.update(markdown_errors)
        prepared_entities.update(markdown_upserts)
        if existing_permalink_by_path is not None:
            for path, prepared_entity in markdown_upserts.items():
                existing_permalink_by_path[path] = prepared_entity.permalink

        regular_upserts, regular_errors = await self._run_bounded(
            regular_paths,
            limit=max_concurrent,
            worker=lambda path: self._upsert_regular_file(files[path]),
        )
        error_by_path.update(regular_errors)
        prepared_entities.update(regular_upserts)

        async with db.scoped_session(self.session_maker) as session:
            refreshed_entities = await self.entity_repository.find_by_ids(
                session, [prepared.entity_id for prepared in prepared_entities.values()]
            )
        entities_by_id = {entity.id: entity for entity in refreshed_entities}

        refreshed, refresh_errors = await self._run_bounded(
            [path for path in ordered_paths if path in prepared_entities],
            limit=(
                self.app_config.index_metadata_update_max_concurrent
                if metadata_update_max_concurrent is None
                else metadata_update_max_concurrent
            ),
            worker=lambda path: self._refresh_search_index(
                prepared_entities[path],
                entities_by_id[prepared_entities[path].entity_id],
            ),
        )
        error_by_path.update(refresh_errors)

        for path in ordered_paths:
            indexed = refreshed.get(path)
            if indexed is not None:
                indexed_entities.append(indexed)

        search_indexed = len(indexed_entities)

        return IndexingBatchResult(
            indexed=indexed_entities,
            errors=[(path, error_by_path[path]) for path in ordered_paths if path in error_by_path],
            search_indexed=search_indexed,
        )

    async def index_markdown_file(
        self,
        file: IndexInputFile,
        *,
        new: bool | None = None,
        existing_permalink_by_path: dict[str, str | None] | None = None,
        index_search: bool = True,
        resolve_relations: bool = True,
    ) -> IndexedEntity:
        """Index one markdown file using the same normalization and upsert path as batches."""
        if not self._is_markdown(file):
            raise ValueError(f"index_markdown_file requires markdown input: {file.path}")

        with logfire.span("index.markdown_file.prepare", path=file.path):
            prepared = await self._prepare_markdown_file(file)
        if existing_permalink_by_path is None:
            with logfire.span("index.markdown_file.load_permalink_map", path=file.path):
                existing_permalink_by_path = await self._get_file_path_to_permalink_map()

        reserved_permalinks = {
            permalink
            for path, permalink in existing_permalink_by_path.items()
            if path != file.path and permalink
        }
        with logfire.span("index.markdown_file.normalize", path=file.path):
            prepared = await self._normalize_markdown_file(prepared, reserved_permalinks)
        existing_permalink_by_path[file.path] = prepared.markdown.frontmatter.permalink

        with logfire.span("index.markdown_file.persist", path=file.path, is_new=new):
            persisted = await self._persist_markdown_file(
                prepared,
                is_new=new,
            )
        existing_permalink_by_path[file.path] = persisted.entity.permalink

        with logfire.span(
            "index.markdown_file.reload_entity",
            path=file.path,
            entity_id=persisted.entity.id,
        ):
            async with db.scoped_session(self.session_maker) as session:
                refreshed = await self.entity_repository.find_by_ids(session, [persisted.entity.id])
        if len(refreshed) != 1:  # pragma: no cover
            raise ValueError(f"Failed to reload indexed entity for {file.path}")
        entity = refreshed[0]
        prepared_entity = await self._build_prepared_entity(
            persisted.prepared,
            entity,
            resolve_relations=resolve_relations,
        )

        if index_search:
            with logfire.span(
                "index.markdown_file.refresh_search_index",
                path=file.path,
                entity_id=entity.id,
            ):
                return await self._refresh_search_index(prepared_entity, entity)

        return IndexedEntity(
            path=prepared_entity.path,
            entity_id=entity.id,
            permalink=entity.permalink,
            checksum=prepared_entity.checksum,
            content_type=prepared_entity.content_type,
            markdown_content=prepared_entity.markdown_content,
            observations=prepared_entity.observations,
            relations=prepared_entity.relations,
            resolve_relations=prepared_entity.resolve_relations,
        )

    async def _get_file_path_to_permalink_map(self) -> dict[str, str | None]:
        """Load current file-path to permalink mappings in a service-owned session."""
        async with db.scoped_session(self.session_maker) as session:
            permalink_by_path: dict[str, str | None] = {
                path: permalink
                for path, permalink in (
                    await self.entity_repository.get_file_path_to_permalink_map(session)
                ).items()
            }
            return permalink_by_path

    # --- Preparation ---

    async def _prepare_markdown_file(self, file: IndexInputFile) -> _PreparedMarkdownFile:
        if file.content is None:
            raise ValueError(f"Missing content for markdown file: {file.path}")

        content = file.content.decode("utf-8")
        file_contains_frontmatter = has_frontmatter(content)
        final_checksum = await self._resolve_checksum(file)
        entity_markdown = await self.entity_service.entity_parser.parse_markdown_content(
            file_path=Path(file.path),
            content=content,
            mtime=file.last_modified.timestamp() if file.last_modified else None,
            ctime=file.created_at.timestamp() if file.created_at else None,
        )

        return _PreparedMarkdownFile(
            file=file,
            content=content,
            final_checksum=final_checksum,
            markdown=entity_markdown,
            file_contains_frontmatter=file_contains_frontmatter,
        )

    async def _normalize_markdown_batch(
        self,
        prepared_markdown: dict[str, _PreparedMarkdownFile],
        *,
        existing_permalink_by_path: dict[str, str | None] | None = None,
    ) -> tuple[dict[str, _PreparedMarkdownFile], dict[str, str]]:
        if not prepared_markdown:
            return {}, {}

        if existing_permalink_by_path is None:
            existing_permalink_by_path = await self._get_file_path_to_permalink_map()

        batch_paths = set(prepared_markdown)
        reserved_permalinks = {
            permalink
            for path, permalink in existing_permalink_by_path.items()
            if path not in batch_paths and permalink
        }

        normalized: dict[str, _PreparedMarkdownFile] = {}
        errors: dict[str, str] = {}

        for path in sorted(prepared_markdown):
            try:
                normalized[path] = await self._normalize_markdown_file(
                    prepared_markdown[path],
                    reserved_permalinks,
                )
                existing_permalink_by_path[path] = normalized[path].markdown.frontmatter.permalink
            except Exception as exc:
                errors[path] = str(exc)
                logger.warning("Batch markdown normalization failed", path=path, error=str(exc))

        return normalized, errors

    async def _normalize_markdown_file(
        self,
        prepared: _PreparedMarkdownFile,
        reserved_permalinks: set[str],
    ) -> _PreparedMarkdownFile:
        final_checksum = prepared.final_checksum
        final_content = prepared.content
        final_permalink = await self._resolve_batch_permalink(prepared, reserved_permalinks)

        # Trigger: markdown file has no frontmatter and sync enforcement is enabled.
        # Why: downstream indexing relies on normalized metadata and stable permalinks.
        # Outcome: write derived metadata back through the storage-agnostic writer.
        if not prepared.file_contains_frontmatter and self.app_config.ensure_frontmatter_on_sync:
            frontmatter_updates = {
                "title": prepared.markdown.frontmatter.title,
                "type": prepared.markdown.frontmatter.type,
                "permalink": final_permalink,
            }
            write_result = await self.file_writer.write_frontmatter(
                IndexFrontmatterUpdate(path=prepared.file.path, metadata=frontmatter_updates)
            )
            final_checksum = write_result.checksum
            final_content = write_result.content
            prepared.markdown.frontmatter.metadata.update(frontmatter_updates)

        # Trigger: existing markdown frontmatter may lack the canonical permalink.
        # Why: batch sync keeps permalinks stable without forcing a full rewrite when unchanged.
        # Outcome: only the permalink field is updated when it actually differs.
        elif (
            prepared.file_contains_frontmatter
            and not self.app_config.disable_permalinks
            and final_permalink != prepared.markdown.frontmatter.permalink
        ):
            prepared.markdown.frontmatter.metadata["permalink"] = final_permalink
            write_result = await self.file_writer.write_frontmatter(
                IndexFrontmatterUpdate(
                    path=prepared.file.path,
                    metadata={"permalink": final_permalink},
                )
            )
            final_checksum = write_result.checksum
            final_content = write_result.content

        return _PreparedMarkdownFile(
            file=prepared.file,
            content=final_content,
            final_checksum=final_checksum,
            markdown=prepared.markdown,
            file_contains_frontmatter=prepared.file_contains_frontmatter,
        )

    async def _resolve_batch_permalink(
        self,
        prepared: _PreparedMarkdownFile,
        reserved_permalinks: set[str],
    ) -> str | None:
        should_resolve_permalink = (
            not prepared.file_contains_frontmatter and self.app_config.ensure_frontmatter_on_sync
        ) or (prepared.file_contains_frontmatter and not self.app_config.disable_permalinks)
        if not should_resolve_permalink:
            permalink = prepared.markdown.frontmatter.permalink
            if permalink:
                reserved_permalinks.add(permalink)
            return permalink

        desired_permalink = await self.entity_service.resolve_permalink(
            prepared.file.path,
            markdown=prepared.markdown,
            skip_conflict_check=True,
        )
        return self._reserve_batch_permalink(desired_permalink, reserved_permalinks)

    def _reserve_batch_permalink(
        self,
        desired_permalink: str,
        reserved_permalinks: set[str],
    ) -> str:
        permalink = desired_permalink
        suffix = 1
        while permalink in reserved_permalinks:
            permalink = f"{desired_permalink}-{suffix}"
            suffix += 1
        reserved_permalinks.add(permalink)
        return permalink

    # --- Persistence ---

    async def _upsert_markdown_file(self, prepared: _PreparedMarkdownFile) -> _PreparedEntity:
        persisted = await self._persist_markdown_file(prepared)
        return await self._build_prepared_entity(persisted.prepared, persisted.entity)

    async def _upsert_regular_file(self, file: IndexInputFile) -> _PreparedEntity:
        checksum = await self._resolve_checksum(file)
        content_type = regular_file_content_type(file)
        async with db.scoped_session(self.session_maker) as session:
            existing = await self.entity_repository.get_by_file_path(
                session, file.path, load_relations=False
            )
            existing_note_content = (
                await NoteContentRepository(
                    project_id=self.relation_repository.project_id
                ).get_by_entity_id(session, existing.id)
                if existing is not None and existing.is_markdown
                else None
            )
            expected_incoming_source_ids = (
                frozenset(
                    int(source_id)
                    for source_id in await session.scalars(
                        select(Relation.from_id).where(
                            Relation.project_id == self.relation_repository.project_id,
                            Relation.to_id == existing.id,
                            Relation.from_id != existing.id,
                        )
                    )
                )
                if (
                    existing is not None
                    and existing.is_markdown
                    and content_type != RUNTIME_MARKDOWN_CONTENT_TYPE
                )
                else frozenset()
            )
        expected_note_db_version = (
            existing_note_content.db_version if existing_note_content is not None else None
        )
        is_new_entity = existing is None

        if existing is None:
            # Non-Markdown resources cannot persist a semantic address back to source bytes.
            # Their stable API identity is external_id; file_path locates the stored resource.
            entity = Entity(
                note_type="file",
                file_path=file.path,
                checksum=checksum,
                title=Path(file.path).name,
                created_at=file.created_at or datetime.now().astimezone(),
                updated_at=file.last_modified or datetime.now().astimezone(),
                content_type=content_type,
                mtime=file.last_modified.timestamp() if file.last_modified else None,
                size=file.size,
            )

            try:
                async with db.scoped_session(self.session_maker) as session:
                    created = await self.entity_repository.add(session, entity)
                entity_id = created.id
            except IntegrityError as exc:
                message = str(exc)
                if (
                    "UNIQUE constraint failed: entity.file_path" in message
                    or "uix_entity_file_path_project" in message
                    or (
                        "duplicate key value violates unique constraint" in message
                        and "file_path" in message
                    )
                ):
                    async with db.scoped_session(self.session_maker) as session:
                        existing = await self.entity_repository.get_by_file_path(
                            session,
                            file.path,
                            load_relations=False,
                        )
                    if existing is None:
                        raise ValueError(
                            f"Entity not found after file_path conflict: {file.path}"
                        ) from exc
                    entity_id = existing.id
                else:
                    raise
        else:
            entity_id = existing.id

        async with db.scoped_session(self.session_maker) as session:
            should_clear_note_state = (
                existing is not None
                and existing.is_markdown
                and content_type != RUNTIME_MARKDOWN_CONTENT_TYPE
            )
            if should_clear_note_state:
                # Accepted-note writers lock NoteContent before Entity. Poison-row
                # repair must use the same order or the two paths can deadlock.
                await lock_note_content_before_entity_mutation(
                    session,
                    project_id=self.relation_repository.project_id,
                    entity_ids=tuple(sorted({entity_id, *expected_incoming_source_ids})),
                )
                fenced_source_ids = set(
                    (
                        await session.scalars(
                            select(NoteContent.entity_id).where(
                                NoteContent.project_id == self.relation_repository.project_id,
                                NoteContent.entity_id.in_(expected_incoming_source_ids),
                            )
                        )
                    ).all()
                )
                if fenced_source_ids != expected_incoming_source_ids:
                    # Legacy sources without NoteContent cannot join the
                    # canonical source-before-target lock order. Leave their
                    # inbound relations untouched until a later indexed pass.
                    return _PreparedEntity(
                        path=file.path,
                        entity_id=entity_id,
                        permalink=existing.permalink,
                        checksum=existing.checksum or checksum,
                        content_type=existing.content_type,
                        search_content=None,
                        resolve_relations=False,
                        refresh_search=False,
                    )
                locked_note_content = await NoteContentRepository(
                    project_id=self.relation_repository.project_id
                ).get_by_entity_id(session, entity_id)
            else:
                locked_note_content = None
            existing = await self.entity_repository.get_by_id(
                session,
                entity_id,
                load_relations=True,
                lock_for_update=True,
            )
            if existing is None:
                raise ValueError(f"Entity not found before file metadata update: {file.path}")
            if should_clear_note_state and locked_note_content is None:
                # A missing row cannot be locked, so recheck after the Entity
                # fence. A completed bootstrap is now visible and must win;
                # a still-absent row is a stable legacy poison row we can repair.
                bootstrapped_note_content = await NoteContentRepository(
                    project_id=self.relation_repository.project_id
                ).get_by_entity_id(
                    session,
                    entity_id,
                )
                if bootstrapped_note_content is not None:
                    return _PreparedEntity(
                        path=file.path,
                        entity_id=existing.id,
                        permalink=existing.permalink,
                        checksum=existing.checksum or checksum,
                        content_type=existing.content_type,
                        search_content=None,
                        resolve_relations=False,
                        refresh_search=False,
                    )
            current_incoming_source_ids = {
                relation.from_id
                for relation in existing.incoming_relations
                if relation.from_id != existing.id
            }
            if should_clear_note_state and not current_incoming_source_ids.issubset(
                expected_incoming_source_ids
            ):
                # A source published a new inbound edge after the fence snapshot.
                # Leave this pass non-destructive so a later pass can lock that
                # source before rewriting its relation.
                return _PreparedEntity(
                    path=file.path,
                    entity_id=existing.id,
                    permalink=existing.permalink,
                    checksum=existing.checksum or checksum,
                    content_type=existing.content_type,
                    search_content=None,
                    resolve_relations=False,
                    refresh_search=False,
                )
            if (
                should_clear_note_state
                and (locked_note_content.db_version if locked_note_content is not None else None)
                != expected_note_db_version
            ):
                # A newer accepted Markdown generation landed after the initial
                # classification read. This resource pass no longer owns cleanup.
                return _PreparedEntity(
                    path=file.path,
                    entity_id=existing.id,
                    permalink=existing.permalink,
                    checksum=existing.checksum or checksum,
                    content_type=existing.content_type,
                    search_content=None,
                    resolve_relations=False,
                    refresh_search=False,
                )
            if (
                not should_clear_note_state
                and existing.is_markdown
                and content_type != RUNTIME_MARKDOWN_CONTENT_TYPE
            ):
                # A newer Markdown pass won between the initial classification
                # read and this lock. Preserve its canonical state and projections;
                # this stale resource pass has nothing left to publish.
                return _PreparedEntity(
                    path=file.path,
                    entity_id=existing.id,
                    permalink=existing.permalink,
                    checksum=existing.checksum or checksum,
                    content_type=existing.content_type,
                    search_content=None,
                    resolve_relations=False,
                    refresh_search=False,
                )
            if existing.is_markdown and content_type != RUNTIME_MARKDOWN_CONTENT_TYPE:
                await self._clear_note_only_state(session, existing)

            metadata_updates = self._resource_metadata_updates(
                file,
                checksum,
                include_created_at=is_new_entity,
            )
            # MIME alone is the downstream note discriminator. A malformed
            # Markdown basename must therefore be normalized both for new rows
            # and for poison rows created by older indexers.
            metadata_updates.update(
                content_type=content_type,
                permalink=None,
                note_type="file",
                title=Path(file.path).name,
                entity_metadata={},
            )
            updated = await self.entity_repository.update(
                session,
                entity_id,
                metadata_updates,
            )
        if updated is None:
            raise ValueError(f"Failed to update file entity metadata for {file.path}")

        return _PreparedEntity(
            path=file.path,
            entity_id=updated.id,
            permalink=updated.permalink,
            checksum=checksum,
            content_type=content_type,
            search_content=None,
            markdown_content=None,
            observations=(),
            relations=(),
            resolve_relations=False,
        )

    async def _clear_note_only_state(self, session: AsyncSession, entity: Entity) -> None:
        """Retire semantic projections when a poison Markdown row becomes a resource."""
        incoming_source_ids = {
            relation.from_id
            for relation in entity.incoming_relations
            if relation.from_id != entity.id
        }
        for relation in entity.incoming_relations:
            relation.to_id = None
            relation.to_entity = None

        await self.observation_repository.delete_by_fields(session, entity_id=entity.id)
        await self.relation_repository.delete_by_fields(session, from_id=entity.id)
        await self.note_content_repository.delete_by_entity_id(session, entity.id)
        await session.execute(
            delete(RelationSearchRefresh).where(
                RelationSearchRefresh.project_id == self.relation_repository.project_id,
                RelationSearchRefresh.entity_id == entity.id,
            )
        )
        session.add_all(
            RelationSearchRefresh(
                project_id=self.relation_repository.project_id,
                entity_id=source_id,
            )
            for source_id in sorted(incoming_source_ids)
        )

    # --- Relations ---

    async def publish_relation_generation(
        self,
        indexed: IndexedEntity,
        *,
        generation: int,
    ) -> bool:
        """Publish one indexed entity's parsed graph after generation claim."""
        return await self.relation_generation_publisher.publish(
            entity_id=indexed.entity_id,
            generation=generation,
            relations=indexed.relations,
            observations=indexed.observations,
        )

    async def publish_relation_generations(
        self,
        indexed_entities: list[IndexedEntity],
        *,
        generation_by_entity_id: Mapping[int, int],
        max_concurrent: int,
    ) -> RelationGenerationBatchResult:
        """Publish claimed batch generations before resolving forward references."""
        indexed_by_path = {
            indexed.path: indexed
            for indexed in indexed_entities
            if indexed.markdown_content is not None and indexed.entity_id in generation_by_entity_id
        }
        published, errors = await self._run_bounded(
            sorted(indexed_by_path),
            limit=max_concurrent,
            worker=lambda path: self.publish_relation_generation(
                indexed_by_path[path],
                generation=generation_by_entity_id[indexed_by_path[path].entity_id],
            ),
        )
        resolvable_entity_ids = [
            indexed_by_path[path].entity_id
            for path in sorted(published)
            if published[path] and indexed_by_path[path].resolve_relations
        ]
        resolved_count = 0
        unresolved_count = 0
        if resolvable_entity_ids:
            resolved_count, unresolved_count = await self._resolve_batch_relations(
                resolvable_entity_ids,
                max_concurrent=max_concurrent,
            )

        _, refresh_errors = await self._run_bounded(
            [path for path in sorted(published) if published[path]],
            limit=max_concurrent,
            worker=lambda path: self.refresh_indexed_entity_search(
                indexed_by_path[path],
                generation=generation_by_entity_id[indexed_by_path[path].entity_id],
            ),
        )
        errors.update(refresh_errors)

        return RelationGenerationBatchResult(
            errors=tuple((path, errors[path]) for path in sorted(errors)),
            relations_resolved=resolved_count,
            relations_unresolved=unresolved_count,
        )

    async def resolve_relation_targets(
        self,
        entity_ids: list[int],
        *,
        max_concurrent: int,
    ) -> tuple[int, int]:
        """Resolve newly published relations through the shared guarded resolver."""
        return await self._resolve_batch_relations(entity_ids, max_concurrent=max_concurrent)

    async def refresh_indexed_entity_search(
        self,
        indexed: IndexedEntity,
        *,
        generation: int,
    ) -> IndexedEntity:
        """Refresh search only while this publication generation remains accepted."""
        async with db.scoped_session(self.session_maker) as session:
            refresh = await self.relation_repository.load_search_refresh_for_generation(
                session,
                entity_id=indexed.entity_id,
                generation=generation,
            )
        # Trigger: a newer accepted note generation won after this publication.
        # Why: combining generation-N parsed markdown with N+1 entity state would
        # produce a search row that never represented one coherent note version.
        # Outcome: terminal-wins; N+1 owns its refresh and this pass leaves durable
        # marker IDs untouched for a later retry.
        if refresh is None:
            return indexed

        try:
            search_content = (
                remove_frontmatter(indexed.markdown_content)
                if indexed.markdown_content is not None
                else None
            )
        except ParseError:
            search_content = indexed.markdown_content

        prepared = _PreparedEntity(
            path=indexed.path,
            entity_id=indexed.entity_id,
            permalink=indexed.permalink,
            checksum=indexed.checksum,
            content_type=indexed.content_type,
            search_content=search_content,
            markdown_content=indexed.markdown_content,
            observations=indexed.observations,
            relations=indexed.relations,
            resolve_relations=indexed.resolve_relations,
        )
        refreshed = await self._refresh_search_index(prepared, refresh.entity)
        async with db.scoped_session(self.session_maker) as session:
            # Trigger: N+1 can be accepted after N loaded its coherent snapshot but
            # before N finishes the external search write.
            # Why: N must not consume the last repair marker after rendering stale
            # bytes; N+1 owns convergence and may already have completed its pass.
            # Outcome: the guarded completion either retires N's observed markers or
            # leaves fresh durable work that repairs a late stale write.
            await self.relation_repository.complete_search_refresh_for_generation(
                session,
                entity_id=indexed.entity_id,
                generation=generation,
                refresh_ids=refresh.refresh_ids,
            )
        return refreshed

    async def _resolve_batch_relations(
        self,
        entity_ids: list[int],
        *,
        max_concurrent: int,
    ) -> tuple[int, int]:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be greater than zero")

        ordered_entity_ids = sorted(set(entity_ids))
        unresolved_relation_lists = await asyncio.gather(
            *(
                self._find_unresolved_relations_for_entity(entity_id)
                for entity_id in ordered_entity_ids
            )
        )
        unresolved_before = sum(len(relations) for relations in unresolved_relation_lists)

        for entity_id in ordered_entity_ids:
            await self.relation_resolution.resolve_relations(entity_id=entity_id)

        remaining_relation_lists = await asyncio.gather(
            *(
                self._find_unresolved_relations_for_entity(entity_id)
                for entity_id in ordered_entity_ids
            )
        )
        remaining_unresolved = sum(len(relations) for relations in remaining_relation_lists)
        return max(0, unresolved_before - remaining_unresolved), remaining_unresolved

    async def _find_unresolved_relations_for_entity(self, entity_id: int):
        """Load unresolved relations for one entity in a service-owned session."""
        async with db.scoped_session(self.session_maker) as session:
            return await self.relation_repository.find_unresolved_relations_for_entity(
                session, entity_id
            )

    # --- Search refresh ---

    async def _refresh_search_index(
        self, prepared: _PreparedEntity, entity: Entity
    ) -> IndexedEntity:
        if prepared.refresh_search:
            try:
                await self.search_service.index_entity_data(entity, content=prepared.search_content)
            except SemanticDependenciesMissingError as exc:
                # Semantic search is optional infrastructure; missing provider deps must not undo
                # the durable file/entity work that already completed.
                logger.warning(
                    "Skipping semantic index refresh because dependencies are unavailable",
                    path=prepared.path,
                    entity_id=entity.id,
                    error=str(exc),
                )
        return IndexedEntity(
            path=prepared.path,
            entity_id=entity.id,
            permalink=entity.permalink,
            checksum=prepared.checksum,
            content_type=prepared.content_type,
            markdown_content=prepared.markdown_content,
            observations=prepared.observations,
            relations=prepared.relations,
            resolve_relations=prepared.resolve_relations,
        )

    # --- Helpers ---

    async def _persist_markdown_file(
        self,
        prepared: _PreparedMarkdownFile,
        *,
        is_new: bool | None = None,
    ) -> _PersistedMarkdownFile:
        async with db.scoped_session(self.session_maker) as session:
            existing = await self.entity_repository.get_by_file_path(
                session,
                prepared.file.path,
                load_relations=False,
            )
            if is_new is None:
                is_new = existing is None
            if is_new:
                entity = await self.entity_service.create_entity_from_markdown(
                    Path(prepared.file.path),
                    prepared.markdown,
                    session=session,
                )
            else:
                entity = await self.entity_service.update_markdown_entity_fields(
                    Path(prepared.file.path),
                    prepared.markdown,
                    existing_entity=existing,
                    session=session,
                )
            prepared = await self._reconcile_persisted_permalink(prepared, entity)
            metadata_updates = self._file_bookkeeping_updates(
                prepared.file,
                prepared.final_checksum,
            )
            updated = await self.entity_repository.update_fields(
                session,
                entity.id,
                metadata_updates,
            )
            if not updated:
                raise ValueError(
                    f"Failed to update markdown entity metadata for {prepared.file.path}"
                )
            self._apply_entity_metadata_updates(entity, metadata_updates)
            return _PersistedMarkdownFile(prepared=prepared, entity=entity)

    async def _reconcile_persisted_permalink(
        self,
        prepared: _PreparedMarkdownFile,
        entity: Entity,
    ) -> _PreparedMarkdownFile:
        # Trigger: the source file started without frontmatter and sync is configured
        #          to leave frontmatterless files alone.
        # Why: upsert may still assign a DB permalink even when disk content should stay untouched.
        # Outcome: skip reconciliation writes that would silently inject frontmatter.
        if (
            self.app_config.disable_permalinks
            or (
                not prepared.file_contains_frontmatter
                and not self.app_config.ensure_frontmatter_on_sync
            )
            or entity.permalink is None
            or entity.permalink == prepared.markdown.frontmatter.permalink
        ):
            return prepared

        logger.debug(
            "Updating permalink after upsert conflict resolution",
            path=prepared.file.path,
            old_permalink=prepared.markdown.frontmatter.permalink,
            new_permalink=entity.permalink,
        )
        prepared.markdown.frontmatter.metadata["permalink"] = entity.permalink
        write_result = await self.file_writer.write_frontmatter(
            IndexFrontmatterUpdate(
                path=prepared.file.path,
                metadata={"permalink": entity.permalink},
            )
        )
        return _PreparedMarkdownFile(
            file=prepared.file,
            content=write_result.content,
            final_checksum=write_result.checksum,
            markdown=prepared.markdown,
            file_contains_frontmatter=prepared.file_contains_frontmatter,
        )

    async def _build_prepared_entity(
        self,
        prepared: _PreparedMarkdownFile,
        entity: Entity,
        *,
        resolve_relations: bool = True,
    ) -> _PreparedEntity:
        indexed_observations = tuple(
            IndexedObservation(
                content=observation.content,
                category=observation.category,
                context=observation.context,
                tags=observation.tags,
            )
            for observation in prepared.markdown.observations
        )
        indexed_relations: list[IndexedRelation] = []
        for relation in prepared.markdown.relations:
            resolved = await self.entity_service.resolve_deferred_self_relation(
                relation.target,
                entity,
            )
            indexed_relations.append(
                IndexedRelation(
                    relation_type=relation.type,
                    target_name=relation.target,
                    context=relation.context,
                    target_id=resolved.id if resolved else None,
                )
            )

        return _PreparedEntity(
            path=prepared.file.path,
            entity_id=entity.id,
            permalink=entity.permalink,
            checksum=prepared.final_checksum,
            content_type=prepared.file.content_type,
            search_content=(
                prepared.markdown.content
                if prepared.markdown.content is not None
                else remove_frontmatter(prepared.content)
            ),
            markdown_content=prepared.content,
            observations=indexed_observations,
            relations=tuple(indexed_relations),
            resolve_relations=resolve_relations,
        )

    async def _resolve_checksum(self, file: IndexInputFile) -> str:
        if file.checksum is not None:
            return file.checksum
        if file.content is None:
            raise ValueError(f"Missing checksum and content for file: {file.path}")
        return await compute_checksum(file.content)

    def _file_bookkeeping_updates(
        self,
        file: IndexInputFile,
        checksum: str,
    ) -> dict[str, object]:
        """Return physical file state without changing note semantics."""
        updates: dict[str, object] = {
            "file_path": file.path,
            "checksum": checksum,
            "size": file.size,
        }
        if file.last_modified is not None:
            updates["mtime"] = file.last_modified.timestamp()
        if file.content_type is not None:
            updates["content_type"] = file.content_type
        return updates

    def _resource_metadata_updates(
        self,
        file: IndexInputFile,
        checksum: str,
        *,
        include_created_at: bool = True,
    ) -> dict[str, object]:
        updates = self._file_bookkeeping_updates(file, checksum)
        if include_created_at and file.created_at is not None:
            updates["created_at"] = file.created_at
        if file.last_modified is not None:
            updates["updated_at"] = file.last_modified
        return updates

    def _apply_entity_metadata_updates(self, entity: Entity, updates: dict[str, object]) -> None:
        """Keep the returned entity aligned with metadata written without reload."""
        for key, value in updates.items():
            setattr(entity, key, value)

    def _is_markdown(self, file: IndexInputFile) -> bool:
        path_is_markdown_note = runtime_file_path_is_markdown_note(Path(file.path).as_posix())
        if file.content_type is not None:
            return file.content_type == RUNTIME_MARKDOWN_CONTENT_TYPE and path_is_markdown_note
        return path_is_markdown_note

    async def _run_bounded(
        self,
        paths: list[str],
        *,
        limit: int,
        worker: Callable[[str], Awaitable[T]],
    ) -> tuple[dict[str, T], dict[str, str]]:
        if not paths:
            return {}, {}

        semaphore = asyncio.Semaphore(limit)
        results: dict[str, T] = {}
        errors: dict[str, str] = {}

        async def run(path: str) -> None:
            async with semaphore:
                try:
                    results[path] = await worker(path)
                except Exception as exc:
                    if isinstance(exc, SyncFatalError) or isinstance(exc.__cause__, SyncFatalError):
                        raise
                    errors[path] = str(exc)
                    logger.warning("Batch indexing failed", path=path, error=str(exc))

        await asyncio.gather(*(run(path) for path in paths))
        return results, errors
