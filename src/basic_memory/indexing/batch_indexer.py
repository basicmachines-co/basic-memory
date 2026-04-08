"""Reusable batch executor for bounded-parallel file indexing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Mapping, TypeVar

from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import compute_checksum, has_frontmatter
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.indexing.models import (
    IndexedEntity,
    IndexFileWriter,
    IndexFrontmatterUpdate,
    IndexingBatchResult,
    IndexInputFile,
)
from basic_memory.models import Entity, Relation
from basic_memory.services import EntityService
from basic_memory.services.exceptions import SyncFatalError
from basic_memory.services.search_service import SearchService
from basic_memory.repository import EntityRepository, RelationRepository

T = TypeVar("T")


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
    checksum: str
    content_type: str | None
    search_content: str | None
    markdown_content: str | None = None


class BatchIndexer:
    """Index already-loaded files without assuming where they came from."""

    def __init__(
        self,
        *,
        app_config: BasicMemoryConfig,
        entity_service: EntityService,
        entity_repository: EntityRepository,
        relation_repository: RelationRepository,
        search_service: SearchService,
        file_writer: IndexFileWriter,
    ) -> None:
        self.app_config = app_config
        self.entity_service = entity_service
        self.entity_repository = entity_repository
        self.relation_repository = relation_repository
        self.search_service = search_service
        self.file_writer = file_writer

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

        refreshed_entities = await self.entity_repository.find_by_ids(
            [prepared.entity_id for prepared in prepared_entities.values()]
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
            existing_permalink_by_path = {
                path: permalink
                for path, permalink in (
                    await self.entity_repository.get_file_path_to_permalink_map()
                ).items()
            }

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
        existing = await self.entity_repository.get_by_file_path(
            prepared.file.path,
            load_relations=False,
        )
        entity = await self.entity_service.upsert_entity_from_markdown(
            Path(prepared.file.path),
            prepared.markdown,
            is_new=existing is None,
        )
        updated = await self.entity_repository.update(
            entity.id,
            self._entity_metadata_updates(prepared.file, prepared.final_checksum),
        )
        if updated is None:
            raise ValueError(f"Failed to update markdown entity metadata for {prepared.file.path}")

        return _PreparedEntity(
            path=prepared.file.path,
            entity_id=updated.id,
            checksum=prepared.final_checksum,
            content_type=prepared.file.content_type,
            search_content=(
                prepared.markdown.content
                if prepared.markdown.content is not None
                else prepared.content
            ),
            markdown_content=prepared.content,
        )

    async def _upsert_regular_file(self, file: IndexInputFile) -> _PreparedEntity:
        checksum = await self._resolve_checksum(file)
        existing = await self.entity_repository.get_by_file_path(file.path, load_relations=False)
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
                created = await self.entity_repository.add(entity)
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
                    existing = await self.entity_repository.get_by_file_path(
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

        updated = await self.entity_repository.update(
            entity_id,
            self._entity_metadata_updates(file, checksum, include_created_at=is_new_entity),
        )
        if updated is None:
            raise ValueError(f"Failed to update file entity metadata for {file.path}")

        return _PreparedEntity(
            path=file.path,
            entity_id=updated.id,
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
            *(
                self.relation_repository.find_unresolved_relations_for_entity(entity_id)
                for entity_id in entity_ids
            )
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
                    resolved_entity = await self.entity_service.link_resolver.resolve_link(
                        relation.to_name
                    )
                    if resolved_entity is None or resolved_entity.id == relation.from_id:
                        return 0

                    try:
                        await self.relation_repository.update(
                            relation.id,
                            {
                                "to_id": resolved_entity.id,
                                "to_name": resolved_entity.title,
                            },
                        )
                    except IntegrityError:
                        await self.relation_repository.delete(relation.id)
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
            *(
                self.relation_repository.find_unresolved_relations_for_entity(entity_id)
                for entity_id in entity_ids
            )
        )
        remaining_unresolved = sum(len(relations) for relations in remaining_relation_lists)

        return sum(resolved_counts), remaining_unresolved

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
