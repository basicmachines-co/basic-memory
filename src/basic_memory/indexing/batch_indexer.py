"""Reusable batch executor for bounded-parallel file indexing."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Mapping, TypeVar

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import logfire
from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import compute_checksum, has_frontmatter, remove_frontmatter
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.indexing.models import (
    IndexEntitySearchWriter,
    IndexedEntity,
    IndexFileWriter,
    IndexFrontmatterUpdate,
    IndexingBatchResult,
    IndexInputFile,
)
from basic_memory.models import Entity, Relation
from basic_memory.services import EntityService
from basic_memory.services.exceptions import SyncFatalError
from basic_memory.repository import EntityRepository, RelationRepository

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class MarkdownOnlyIndexEntitySearchWriter:
    """Filter regular file entities out of batch search indexing."""

    search_writer: IndexEntitySearchWriter

    async def index_entity_data(self, entity: Entity, content: str | None = None) -> None:
        if not entity.is_markdown:
            return

        await self.search_writer.index_entity_data(entity, content=content)


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


@dataclass(slots=True)
class _PersistedMarkdownFile:
    prepared: _PreparedMarkdownFile
    entity: Entity


class BatchIndexer:
    """Index already-loaded files without assuming where they came from."""

    def __init__(
        self,
        *,
        app_config: BasicMemoryConfig,
        entity_service: EntityService,
        entity_repository: EntityRepository,
        relation_repository: RelationRepository,
        search_service: IndexEntitySearchWriter,
        file_writer: IndexFileWriter,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self.app_config = app_config
        self.entity_service = entity_service
        self.entity_repository = entity_repository
        self.relation_repository = relation_repository
        self.search_service = search_service
        self.file_writer = file_writer
        self.session_maker = session_maker

    @asynccontextmanager
    async def _session_scope(
        self, session: AsyncSession | None = None
    ) -> AsyncIterator[AsyncSession]:
        """Use the caller's session or open a service-owned transaction."""
        if session is not None:
            yield session
            return

        async with db.scoped_session(self.session_maker) as owned_session:
            yield owned_session

    async def index_files(
        self,
        files: Mapping[str, IndexInputFile],
        *,
        max_concurrent: int,
        parse_max_concurrent: int | None = None,
        existing_permalink_by_path: dict[str, str | None] | None = None,
    ) -> IndexingBatchResult:
        """Index one batch of loaded files with bounded concurrency."""
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be greater than zero")

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
        resolved_count = 0
        unresolved_count = 0
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

        markdown_entity_ids = [
            prepared_entities[path].entity_id
            for path in markdown_paths
            if path in prepared_entities
        ]
        if markdown_entity_ids:
            resolved_count, unresolved_count = await self._resolve_batch_relations(
                markdown_entity_ids,
                max_concurrent=max_concurrent,
            )

        async with self._session_scope() as session:
            refreshed_entities = await self.entity_repository.find_by_ids(
                session, [prepared.entity_id for prepared in prepared_entities.values()]
            )
        entities_by_id = {entity.id: entity for entity in refreshed_entities}

        refreshed, refresh_errors = await self._run_bounded(
            [path for path in ordered_paths if path in prepared_entities],
            limit=self.app_config.index_metadata_update_max_concurrent,
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
            relations_resolved=resolved_count,
            relations_unresolved=unresolved_count,
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
                resolve_relations=resolve_relations,
                reload_entity=False,
            )
        existing_permalink_by_path[file.path] = persisted.entity.permalink

        with logfire.span(
            "index.markdown_file.reload_entity",
            path=file.path,
            entity_id=persisted.entity.id,
        ):
            async with self._session_scope() as session:
                refreshed = await self.entity_repository.find_by_ids(session, [persisted.entity.id])
        if len(refreshed) != 1:  # pragma: no cover
            raise ValueError(f"Failed to reload indexed entity for {file.path}")
        entity = refreshed[0]
        prepared_entity = self._build_prepared_entity(persisted.prepared, entity)

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
        )

    async def _get_file_path_to_permalink_map(self) -> dict[str, str | None]:
        """Load current file-path to permalink mappings in a service-owned session."""
        async with self._session_scope() as session:
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
        return self._build_prepared_entity(persisted.prepared, persisted.entity)

    async def _upsert_regular_file(self, file: IndexInputFile) -> _PreparedEntity:
        checksum = await self._resolve_checksum(file)
        async with self._session_scope() as session:
            existing = await self.entity_repository.get_by_file_path(
                session, file.path, load_relations=False
            )
        is_new_entity = existing is None

        if existing is None:
            await self.entity_service.resolve_permalink(file.path, skip_conflict_check=True)
            entity = Entity(
                note_type="file",
                file_path=file.path,
                checksum=checksum,
                title=Path(file.path).name,
                created_at=file.created_at or datetime.now().astimezone(),
                updated_at=file.last_modified or datetime.now().astimezone(),
                content_type=file.content_type or "text/plain",
                mtime=file.last_modified.timestamp() if file.last_modified else None,
                size=file.size,
            )

            try:
                async with self._session_scope() as session:
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
                    async with self._session_scope() as session:
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

        async with self._session_scope() as session:
            updated = await self.entity_repository.update(
                session,
                entity_id,
                self._entity_metadata_updates(file, checksum, include_created_at=is_new_entity),
            )
        if updated is None:
            raise ValueError(f"Failed to update file entity metadata for {file.path}")

        return _PreparedEntity(
            path=file.path,
            entity_id=updated.id,
            permalink=updated.permalink,
            checksum=checksum,
            content_type=file.content_type,
            search_content=None,
            markdown_content=None,
        )

    # --- Relations ---

    async def _resolve_batch_relations(
        self,
        entity_ids: list[int],
        *,
        max_concurrent: int,
    ) -> tuple[int, int]:
        unresolved_relation_lists = await asyncio.gather(
            *(self._find_unresolved_relations_for_entity(entity_id) for entity_id in entity_ids)
        )
        unresolved_relations = [
            relation for relation_list in unresolved_relation_lists for relation in relation_list
        ]

        if not unresolved_relations:
            return 0, 0

        semaphore = asyncio.Semaphore(max_concurrent)

        async def resolve_relation(relation: Relation) -> int:
            async with semaphore:
                try:
                    # strict=True for deferred resolution: only fill in to_id on an
                    # exact permalink/title/file_path match. Fuzzy fallback would silently
                    # resolve ambiguous links to whichever entity shares tokens with the
                    # link text, mismatching this with the sync_service forward-reference
                    # path and producing confidently-wrong graph edges. See
                    # sync_service.resolve_forward_references for the same change.
                    async with self._session_scope() as session:
                        resolved_entity = await self.entity_service.link_resolver.resolve_link(
                            relation.to_name, strict=True, session=session
                        )
                    if resolved_entity is None or resolved_entity.id == relation.from_id:
                        return 0

                    try:
                        async with self._session_scope() as session:
                            await self.relation_repository.update(
                                session,
                                relation.id,
                                {
                                    "to_id": resolved_entity.id,
                                    "to_name": resolved_entity.title,
                                },
                            )
                    except IntegrityError:
                        async with self._session_scope() as session:
                            await self.relation_repository.delete(session, relation.id)
                    return 1
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.warning(
                        "Batch relation resolution failed",
                        relation_id=relation.id,
                        from_id=relation.from_id,
                        to_name=relation.to_name,
                        error=str(exc),
                    )
                    return 0

        resolved_counts = await asyncio.gather(
            *(resolve_relation(relation) for relation in unresolved_relations)
        )

        remaining_relation_lists = await asyncio.gather(
            *(self._find_unresolved_relations_for_entity(entity_id) for entity_id in entity_ids)
        )
        remaining_unresolved = sum(len(relations) for relations in remaining_relation_lists)

        return sum(resolved_counts), remaining_unresolved

    async def _find_unresolved_relations_for_entity(self, entity_id: int):
        """Load unresolved relations for one entity in a service-owned session."""
        async with self._session_scope() as session:
            return await self.relation_repository.find_unresolved_relations_for_entity(
                session, entity_id
            )

    # --- Search refresh ---

    async def _refresh_search_index(
        self, prepared: _PreparedEntity, entity: Entity
    ) -> IndexedEntity:
        await self.search_service.index_entity_data(entity, content=prepared.search_content)
        return IndexedEntity(
            path=prepared.path,
            entity_id=entity.id,
            permalink=entity.permalink,
            checksum=prepared.checksum,
            content_type=prepared.content_type,
            markdown_content=prepared.markdown_content,
        )

    # --- Helpers ---

    async def _persist_markdown_file(
        self,
        prepared: _PreparedMarkdownFile,
        *,
        is_new: bool | None = None,
        resolve_relations: bool = True,
        reload_entity: bool = True,
    ) -> _PersistedMarkdownFile:
        async with self._session_scope() as session:
            existing = await self.entity_repository.get_by_file_path(
                session,
                prepared.file.path,
                load_relations=False,
            )
            if is_new is None:
                is_new = existing is None
            entity = await self.entity_service.upsert_entity_from_markdown(
                Path(prepared.file.path),
                prepared.markdown,
                is_new=is_new,
                existing_entity=existing,
                resolve_relations=resolve_relations,
                reload_entity=reload_entity,
                session=session,
            )
            prepared = await self._reconcile_persisted_permalink(prepared, entity)
            metadata_updates = self._entity_metadata_updates(prepared.file, prepared.final_checksum)
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

    def _build_prepared_entity(
        self,
        prepared: _PreparedMarkdownFile,
        entity: Entity,
    ) -> _PreparedEntity:
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
        )

    async def _resolve_checksum(self, file: IndexInputFile) -> str:
        if file.checksum is not None:
            return file.checksum
        if file.content is None:
            raise ValueError(f"Missing checksum and content for file: {file.path}")
        return await compute_checksum(file.content)

    def _entity_metadata_updates(
        self,
        file: IndexInputFile,
        checksum: str,
        *,
        include_created_at: bool = True,
    ) -> dict[str, object]:
        updates: dict[str, object] = {
            "file_path": file.path,
            "checksum": checksum,
            "size": file.size,
        }
        if include_created_at and file.created_at is not None:
            updates["created_at"] = file.created_at
        if file.last_modified is not None:
            updates["updated_at"] = file.last_modified
            updates["mtime"] = file.last_modified.timestamp()
        if file.content_type is not None:
            updates["content_type"] = file.content_type
        return updates

    def _apply_entity_metadata_updates(self, entity: Entity, updates: dict[str, object]) -> None:
        """Keep the returned entity aligned with metadata written without reload."""
        for key, value in updates.items():
            setattr(entity, key, value)

    def _is_markdown(self, file: IndexInputFile) -> bool:
        if file.content_type is not None:
            return file.content_type == "text/markdown"
        return Path(file.path).suffix.lower() in {".md", ".markdown"}

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
