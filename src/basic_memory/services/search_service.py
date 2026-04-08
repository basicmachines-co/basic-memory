"""Service for search operations."""

import asyncio
import ast
import re
from datetime import datetime
from typing import List, Optional, Set, Dict, Any

from dateparser import parse
from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy import text

from basic_memory import telemetry
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository
from basic_memory.repository.search_repository import (
    SearchIndexRow,
    SearchRepository,
    VectorSyncBatchResult,
)
from basic_memory.schemas.search import SearchQuery, SearchItemType, SearchRetrievalMode
from basic_memory.services import FileService

# Maximum size for content_stems field to stay under Postgres's 8KB index row limit.
# We use 6000 characters to leave headroom for other indexed columns and overhead.
MAX_CONTENT_STEMS_SIZE = 6000

# Common glue words used to relax natural-language FTS queries after strict misses.
FTS_RELAXED_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "the",
    "their",
    "this",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
    "you",
    "your",
}


def _strip_nul(value: str) -> str:
    """Strip NUL bytes that PostgreSQL text columns cannot store.

    rclone preallocation on virtual filesystems (e.g. Google Drive File Stream)
    can pad files with \\x00 bytes. See: rclone/rclone#6801
    """
    return value.replace("\x00", "")


def _mtime_to_datetime(entity: Entity) -> datetime:
    """Convert entity mtime (file modification time) to datetime.

    Returns the file's actual modification time, falling back to updated_at
    if mtime is not available.
    """
    if entity.mtime:
        return datetime.fromtimestamp(entity.mtime).astimezone()
    return entity.updated_at


class SearchService:
    """Service for search operations.

    Supports three primary search modes:
    1. Exact permalink lookup
    2. Pattern matching with * (e.g., 'specs/*')
    3. Full-text search across title/content
    """

    def __init__(
        self,
        search_repository: SearchRepository,
        entity_repository: EntityRepository,
        file_service: FileService,
    ):
        self.repository = search_repository
        self.entity_repository = entity_repository
        self.file_service = file_service

    async def init_search_index(self):
        """Create FTS5 virtual table if it doesn't exist."""
        await self.repository.init_search_index()

    async def reindex_all(self, background_tasks: Optional[BackgroundTasks] = None) -> None:
        """Reindex all content from database."""

        logger.info("Starting full reindex")
        # Clear and recreate search index
        await self.repository.execute_query(text("DROP TABLE IF EXISTS search_index"), params={})
        await self.repository.execute_query(
            text("DROP TABLE IF EXISTS search_vector_embeddings"), params={}
        )
        await self.repository.execute_query(
            text("DROP TABLE IF EXISTS search_vector_chunks"), params={}
        )
        await self.repository.execute_query(
            text("DROP TABLE IF EXISTS search_vector_index"), params={}
        )
        await self.init_search_index()

        # Reindex all entities
        logger.debug("Indexing entities")
        entities = await self.entity_repository.find_all()
        for entity in entities:
            await self.index_entity(entity, background_tasks)

        logger.info("Reindex complete")

    async def search(self, query: SearchQuery, limit=10, offset=0) -> List[SearchIndexRow]:
        """Search across all indexed content.

        Supports three modes:
        1. Exact permalink: finds direct matches for a specific path
        2. Pattern match: handles * wildcards in paths
        3. Text search: full-text search across title/content
        """
        # Support tag:<tag> shorthand by mapping to tags filter
        if query.text:
            text = query.text.strip()
            if text.lower().startswith("tag:"):
                tag_values = re.split(r"[,\s]+", text[4:].strip())
                tags = [t for t in tag_values if t]
                if tags:
                    query.tags = tags
                    query.text = None

        if query.no_criteria():
            logger.debug("no criteria passed to query")
            return []

        after_date = (
            (
                query.after_date
                if isinstance(query.after_date, datetime)
                else parse(query.after_date)
            )
            if query.after_date
            else None
        )

        # Merge structured metadata filters (explicit + convenience fields)
        metadata_filters: Optional[Dict[str, Any]] = None
        if query.metadata_filters or query.tags or query.status:
            metadata_filters = dict(query.metadata_filters or {})
            if query.tags:
                metadata_filters.setdefault("tags", query.tags)
            if query.status:
                metadata_filters.setdefault("status", query.status)

        retrieval_mode = query.retrieval_mode or SearchRetrievalMode.FTS
        strict_search_text = query.text
        has_query = bool(
            strict_search_text or query.title or query.permalink or query.permalink_match
        )
        has_filters = bool(
            metadata_filters
            or query.note_types
            or query.entity_types
            or after_date
            or query.tags
            or query.status
        )

        with telemetry.scope(
            "search.execute",
            retrieval_mode=retrieval_mode.value,
            has_query=has_query,
            has_filters=has_filters,
            limit=limit,
            offset=offset,
        ):
            logger.trace(f"Searching with query: {query}")
            with telemetry.scope(
                "search.repository_query",
                retrieval_mode=retrieval_mode.value,
                phase="repository_query",
                has_query=has_query,
                has_filters=has_filters,
            ):
                # First pass: preserve existing strict search behavior.
                results = await self.repository.search(
                    search_text=strict_search_text,
                    permalink=query.permalink,
                    permalink_match=query.permalink_match,
                    title=query.title,
                    note_types=query.note_types,
                    search_item_types=query.entity_types,
                    after_date=after_date,
                    metadata_filters=metadata_filters,
                    retrieval_mode=retrieval_mode,
                    min_similarity=query.min_similarity,
                    limit=limit,
                    offset=offset,
                )

        # Trigger: strict FTS with plain multi-term text returned no results.
        # Why: natural-language queries often include stopwords that over-constrain implicit AND.
        # Outcome: retry once with relaxed OR terms while preserving explicit boolean intent.
        if results:
            return results
        if not self._is_relaxed_fts_fallback_eligible(query, strict_search_text, retrieval_mode):
            return results

        assert strict_search_text is not None
        relaxed_search_text = self._build_relaxed_fts_query(strict_search_text)
        if relaxed_search_text == strict_search_text:
            return results

        logger.debug(
            "Strict FTS returned 0 results; retrying relaxed FTS query "
            f"strict='{strict_search_text}' relaxed='{relaxed_search_text}'"
        )
        with telemetry.scope(
            "search.relaxed_fts_retry",
            retrieval_mode=retrieval_mode.value,
            token_count=len(self._tokenize_fts_text(strict_search_text)),
            limit=limit,
            offset=offset,
        ):
            with telemetry.scope(
                "search.repository_query",
                retrieval_mode=retrieval_mode.value,
                phase="repository_query",
                has_query=has_query,
                has_filters=has_filters,
            ):
                return await self.repository.search(
                    search_text=relaxed_search_text,
                    permalink=query.permalink,
                    permalink_match=query.permalink_match,
                    title=query.title,
                    note_types=query.note_types,
                    search_item_types=query.entity_types,
                    after_date=after_date,
                    metadata_filters=metadata_filters,
                    retrieval_mode=retrieval_mode,
                    min_similarity=query.min_similarity,
                    limit=limit,
                    offset=offset,
                )

    @staticmethod
    def _tokenize_fts_text(search_text: str) -> list[str]:
        """Tokenize text into alphanumeric terms for relaxed FTS fallback."""
        return re.findall(r"[A-Za-z0-9]+", search_text.lower())

    @classmethod
    def _build_relaxed_fts_query(cls, search_text: str) -> str:
        """Build a less strict OR query from natural-language input."""
        normalized_terms = cls._tokenize_fts_text(search_text)
        if not normalized_terms:
            return search_text

        deduped_terms: list[str] = []
        seen_terms: set[str] = set()
        for term in normalized_terms:
            if term in seen_terms:
                continue
            seen_terms.add(term)
            deduped_terms.append(term)

        pruned_terms = [term for term in deduped_terms if term not in FTS_RELAXED_STOPWORDS]
        relaxed_terms = pruned_terms or deduped_terms
        return " OR ".join(relaxed_terms)

    @classmethod
    def _is_relaxed_fts_fallback_eligible(
        cls,
        query: SearchQuery,
        search_text: str | None,
        retrieval_mode: SearchRetrievalMode,
    ) -> bool:
        """Check whether we should run relaxed OR fallback after strict FTS returns empty."""
        if retrieval_mode != SearchRetrievalMode.FTS:
            return False
        if not search_text or not search_text.strip():
            return False
        if '"' in search_text:
            return False
        if query.has_boolean_operators():
            return False
        tokens = cls._tokenize_fts_text(search_text)
        # Trigger: query has only one or two terms (e.g., link titles like "New Feature").
        # Why: OR-relaxing short queries can over-broaden and produce false positives.
        # Outcome: require at least three tokens before enabling relaxed fallback.
        if len(tokens) < 3:
            return False
        # Trigger: query contains explicit numeric identifiers (e.g., "root note 1").
        # Why: OR-relaxing identifier-like queries can over-broaden and create false positives.
        # Outcome: preserve strict matching for these targeted queries.
        if any(token.isdigit() for token in tokens):
            return False
        return True

    @staticmethod
    def _generate_variants(text: str) -> Set[str]:
        """Generate text variants for better fuzzy matching.

        Creates variations of the text to improve match chances:
        - Original form
        - Lowercase form
        - Path segments (for permalinks)
        - Common word boundaries
        """
        variants = {text, text.lower()}

        # Add path segments
        if "/" in text:
            variants.update(p.strip() for p in text.split("/") if p.strip())

        # Add word boundaries
        variants.update(w.strip() for w in text.lower().split() if w.strip())

        # Trigrams disabled: They create massive search index bloat, increasing DB size significantly
        # and slowing down indexing performance. FTS5 search works well without them.
        # See: https://github.com/basicmachines-co/basic-memory/issues/351
        # variants.update(text[i : i + 3].lower() for i in range(len(text) - 2))

        return variants

    def _extract_entity_tags(self, entity: Entity) -> List[str]:
        """Extract tags from entity metadata for search indexing.

        Handles multiple tag formats:
        - List format: ["tag1", "tag2"]
        - String format: "['tag1', 'tag2']" or "[tag1, tag2]"
        - Empty: [] or "[]"

        Returns a list of tag strings for search indexing.
        """
        if not entity.entity_metadata or "tags" not in entity.entity_metadata:
            return []

        tags = entity.entity_metadata["tags"]

        # Handle list format (preferred)
        if isinstance(tags, list):
            return [str(tag) for tag in tags if tag]

        # Handle string format (legacy)
        if isinstance(tags, str):
            try:
                # Parse string representation of list
                parsed_tags = ast.literal_eval(tags)
                if isinstance(parsed_tags, list):
                    return [str(tag) for tag in parsed_tags if tag]
            except (ValueError, SyntaxError):
                # If parsing fails, treat as single tag
                return [tags] if tags.strip() else []

        return []  # pragma: no cover

    async def index_entity(
        self,
        entity: Entity,
        background_tasks: Optional[BackgroundTasks] = None,
        content: str | None = None,
    ) -> None:
        if background_tasks:
            background_tasks.add_task(self.index_entity_data, entity, content)
        else:
            await self.index_entity_data(entity, content)

    async def index_entity_data(
        self,
        entity: Entity,
        content: str | None = None,
    ) -> None:
        logger.debug(
            f"[BackgroundTask] Starting search index for entity_id={entity.id} "
            f"permalink={entity.permalink} project_id={entity.project_id}"
        )
        try:
            with telemetry.scope(
                "search.index_entity_data",
                phase="index_entity_data",
                result_count=1,
            ):
                with telemetry.scope(
                    "search.index.delete_existing",
                    phase="delete_existing",
                    result_count=1,
                ):
                    await self.repository.delete_by_entity_id(entity_id=entity.id)

                if entity.is_markdown:
                    await self.index_entity_markdown(entity, content)
                else:
                    await self.index_entity_file(entity)

            logger.debug(
                f"[BackgroundTask] Completed search index for entity_id={entity.id} "
                f"permalink={entity.permalink}"
            )
        except Exception as e:  # pragma: no cover
            # Background task failure logging; exceptions are re-raised.
            # Avoid forcing synthetic failures just for line coverage.
            logger.error(  # pragma: no cover
                f"[BackgroundTask] Failed search index for entity_id={entity.id} "
                f"permalink={entity.permalink} error={e}"
            )
            raise  # pragma: no cover

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Refresh vector chunks for one entity in repositories that support semantic indexing."""
        entity = await self.entity_repository.find_by_id(entity_id)
        if entity is None:
            await self._clear_entity_vectors(entity_id)
            return

        if not self._entity_embeddings_enabled(entity):
            await self._clear_entity_vectors(entity_id)
            return

        await self.repository.sync_entity_vectors(entity_id)

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
        progress_callback=None,
    ) -> VectorSyncBatchResult:
        """Refresh vector chunks for a batch of entities."""
        if not entity_ids:
            return VectorSyncBatchResult(
                entities_total=0,
                entities_synced=0,
                entities_failed=0,
            )

        entities_by_id = {
            entity.id: entity for entity in await self.entity_repository.find_by_ids(entity_ids)
        }
        unknown_ids = [entity_id for entity_id in entity_ids if entity_id not in entities_by_id]
        opted_out_ids = [
            entity_id
            for entity_id in entity_ids
            if (
                (entity := entities_by_id.get(entity_id)) is not None
                and not self._entity_embeddings_enabled(entity)
            )
        ]
        if opted_out_ids:
            await asyncio.gather(
                *(self._clear_entity_vectors(entity_id) for entity_id in opted_out_ids)
            )

        eligible_entity_ids = [
            entity_id
            for entity_id in entity_ids
            if entity_id in entities_by_id and entity_id not in opted_out_ids
        ]

        cleanup_task = (
            self.repository.sync_entity_vectors_batch(unknown_ids) if unknown_ids else None
        )
        eligible_task = (
            self.repository.sync_entity_vectors_batch(
                eligible_entity_ids,
                progress_callback=progress_callback,
            )
            if eligible_entity_ids
            else None
        )
        repository_results = [
            result
            for result in await asyncio.gather(
                cleanup_task if cleanup_task is not None else asyncio.sleep(0, result=None),
                eligible_task if eligible_task is not None else asyncio.sleep(0, result=None),
            )
            if result is not None
        ]

        if not repository_results:
            return VectorSyncBatchResult(
                entities_total=len(entity_ids),
                entities_synced=0,
                entities_failed=0,
                entities_skipped=len(opted_out_ids),
            )

        batch_result = VectorSyncBatchResult(
            entities_total=len(entity_ids),
            entities_synced=sum(result.entities_synced for result in repository_results),
            entities_failed=sum(result.entities_failed for result in repository_results),
            entities_deferred=sum(result.entities_deferred for result in repository_results),
            entities_skipped=(
                len(opted_out_ids)
                + sum(result.entities_skipped for result in repository_results)
                - len(unknown_ids)
            ),
            failed_entity_ids=[
                failed_entity_id
                for result in repository_results
                for failed_entity_id in result.failed_entity_ids
            ],
            chunks_total=sum(result.chunks_total for result in repository_results),
            chunks_skipped=sum(result.chunks_skipped for result in repository_results),
            embedding_jobs_total=sum(result.embedding_jobs_total for result in repository_results),
            prepare_seconds_total=sum(result.prepare_seconds_total for result in repository_results),
            queue_wait_seconds_total=sum(
                result.queue_wait_seconds_total for result in repository_results
            ),
            embed_seconds_total=sum(result.embed_seconds_total for result in repository_results),
            write_seconds_total=sum(result.write_seconds_total for result in repository_results),
        )
        return batch_result

    async def reindex_vectors(self, progress_callback=None) -> dict:
        """Rebuild vector embeddings for all entities.

        Args:
            progress_callback: Optional callable(entity_id, index, total) for progress reporting.

        Returns:
            dict with stats: total_entities, embedded, skipped, errors
        """
        entities = await self.entity_repository.find_all()
        entity_ids = [entity.id for entity in entities]

        # Clean up stale rows in search_index and search_vector_chunks
        # that reference entity_ids no longer in the entity table
        await self._purge_stale_search_rows()

        batch_result = await self.sync_entity_vectors_batch(
            entity_ids,
            progress_callback=progress_callback,
        )
        stats = {
            "total_entities": batch_result.entities_total,
            "embedded": batch_result.entities_synced,
            "skipped": batch_result.entities_skipped,
            "errors": batch_result.entities_failed,
        }

        for failed_entity_id in batch_result.failed_entity_ids:
            logger.warning(f"Failed to embed entity {failed_entity_id}")

        return stats

    async def _purge_stale_search_rows(self) -> None:
        """Remove rows from search_index and search_vector_chunks for deleted entities.

        Trigger: entities are deleted but their derived search rows remain
        Why: stale rows inflate embedding coverage stats in project info
        Outcome: search tables only contain rows for entities that still exist
        """
        from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
        from sqlalchemy import text

        project_id = self.repository.project_id
        stale_entity_filter = (
            "entity_id NOT IN (SELECT id FROM entity WHERE project_id = :project_id)"
        )
        params = {"project_id": project_id}

        # Delete stale search_index rows
        await self.repository.execute_query(
            text(
                f"DELETE FROM search_index WHERE project_id = :project_id AND {stale_entity_filter}"
            ),
            params,
        )

        # SQLite vec has no CASCADE — must delete embeddings before chunks
        if isinstance(self.repository, SQLiteSearchRepository):
            await self.repository.execute_query(
                text(
                    "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                    "SELECT id FROM search_vector_chunks "
                    f"WHERE project_id = :project_id AND {stale_entity_filter})"
                ),
                params,
            )

        # Postgres CASCADE handles embedding deletion automatically
        await self.repository.execute_query(
            text(
                f"DELETE FROM search_vector_chunks "
                f"WHERE project_id = :project_id AND {stale_entity_filter}"
            ),
            params,
        )

        logger.info("Purged stale search rows for deleted entities", project_id=project_id)

    @staticmethod
    def _entity_embeddings_enabled(entity: Entity) -> bool:
        """Return whether semantic embeddings should be generated for this entity."""
        if not entity.entity_metadata:
            return True

        embed_value = entity.entity_metadata.get("embed")
        if embed_value is None:
            return True
        if isinstance(embed_value, bool):
            return embed_value
        if isinstance(embed_value, str):
            normalized = embed_value.strip().lower()
            if normalized in {"false", "0", "no", "off"}:
                return False
            if normalized in {"true", "1", "yes", "on"}:
                return True
        if isinstance(embed_value, (int, float)):
            return bool(embed_value)

        # Default unknown values to enabled so malformed metadata does not silently
        # remove notes from semantic search.
        return True

    async def _clear_entity_vectors(self, entity_id: int) -> None:
        """Delete derived vector rows for one entity."""
        from basic_memory.repository.search_repository_base import SearchRepositoryBase
        from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

        # Trigger: semantic indexing is disabled for this repository instance.
        # Why: repositories only create vector tables when semantic search is enabled.
        # Outcome: skip cleanup because there are no active derived vector rows to maintain.
        if isinstance(self.repository, SearchRepositoryBase) and not self.repository._semantic_enabled:
            return

        params = {"project_id": self.repository.project_id, "entity_id": entity_id}
        if isinstance(self.repository, SQLiteSearchRepository):
            await self.repository.execute_query(
                text(
                    "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id)"
                ),
                params,
            )

        await self.repository.execute_query(
            text(
                "DELETE FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            params,
        )

    async def index_entity_file(
        self,
        entity: Entity,
    ) -> None:
        with telemetry.scope(
            "search.index_file",
            phase="index_file",
            result_count=1,
        ):
            # Index entity file with no content
            await self.repository.index_item(
                SearchIndexRow(
                    id=entity.id,
                    entity_id=entity.id,
                    type=SearchItemType.ENTITY.value,
                    title=_strip_nul(entity.title),
                    permalink=entity.permalink,  # Required for Postgres NOT NULL constraint
                    file_path=entity.file_path,
                    metadata={
                        "note_type": entity.note_type,
                    },
                    created_at=entity.created_at,
                    updated_at=_mtime_to_datetime(entity),
                    project_id=entity.project_id,
                )
            )

    async def index_entity_markdown(
        self,
        entity: Entity,
        content: str | None = None,
    ) -> None:
        """Index an entity and all its observations and relations.

        Args:
            entity: The entity to index
            content: Optional pre-loaded content (avoids file read). If None, will read from file.

        Indexing structure:
        1. Entities
           - permalink: direct from entity (e.g., "specs/search")
           - file_path: physical file location
           - project_id: project context for isolation

        2. Observations
           - permalink: entity permalink + /observations/id (e.g., "specs/search/observations/123")
           - file_path: parent entity's file (where observation is defined)
           - project_id: inherited from parent entity

        3. Relations (only index outgoing relations defined in this file)
           - permalink: from_entity/relation_type/to_entity (e.g., "specs/search/implements/features/search-ui")
           - file_path: source entity's file (where relation is defined)
           - project_id: inherited from source entity

        Each type gets its own row in the search index with appropriate metadata.
        The project_id is automatically added by the repository when indexing.
        """

        with telemetry.scope(
            "search.index_markdown",
            phase="index_markdown",
            result_count=1,
        ):
            rows_to_index = []

            content_stems = []
            content_snippet = ""
            title_variants = self._generate_variants(entity.title)
            content_stems.extend(title_variants)

            if content is None:
                with telemetry.scope(
                    "search.index.read_content",
                    phase="read_content",
                    result_count=1,
                ):
                    content = await self.file_service.read_entity_content(entity)
            if content:
                content_stems.append(content)
                content_snippet = _strip_nul(content)

            with telemetry.scope(
                "search.index.build_rows",
                phase="build_rows",
                result_count=1,
            ):
                if entity.permalink:
                    content_stems.extend(self._generate_variants(entity.permalink))

                content_stems.extend(self._generate_variants(entity.file_path))

                entity_tags = self._extract_entity_tags(entity)
                if entity_tags:
                    content_stems.extend(entity_tags)

                entity_content_stems = _strip_nul(
                    "\n".join(p for p in content_stems if p and p.strip())
                )

                if len(entity_content_stems) > MAX_CONTENT_STEMS_SIZE:  # pragma: no cover
                    entity_content_stems = entity_content_stems[
                        :MAX_CONTENT_STEMS_SIZE
                    ]  # pragma: no cover

                rows_to_index.append(
                    SearchIndexRow(
                        id=entity.id,
                        type=SearchItemType.ENTITY.value,
                        title=_strip_nul(entity.title),
                        content_stems=entity_content_stems,
                        content_snippet=content_snippet,
                        permalink=entity.permalink,
                        file_path=entity.file_path,
                        entity_id=entity.id,
                        metadata={
                            "note_type": entity.note_type,
                        },
                        created_at=entity.created_at,
                        updated_at=_mtime_to_datetime(entity),
                        project_id=entity.project_id,
                    )
                )

                seen_permalinks: set[str] = {entity.permalink} if entity.permalink else set()
                for obs in entity.observations:
                    obs_permalink = obs.permalink
                    if obs_permalink in seen_permalinks:
                        logger.debug(f"Skipping duplicate observation permalink: {obs_permalink}")
                        continue
                    seen_permalinks.add(obs_permalink)

                    obs_content_stems = _strip_nul(
                        "\n".join(
                            p for p in self._generate_variants(obs.content) if p and p.strip()
                        )
                    )
                    if len(obs_content_stems) > MAX_CONTENT_STEMS_SIZE:  # pragma: no cover
                        obs_content_stems = obs_content_stems[
                            :MAX_CONTENT_STEMS_SIZE
                        ]  # pragma: no cover
                    rows_to_index.append(
                        SearchIndexRow(
                            id=obs.id,
                            type=SearchItemType.OBSERVATION.value,
                            title=_strip_nul(f"{obs.category}: {obs.content[:100]}..."),
                            content_stems=obs_content_stems,
                            content_snippet=_strip_nul(obs.content),
                            permalink=obs_permalink,
                            file_path=entity.file_path,
                            category=obs.category,
                            entity_id=entity.id,
                            metadata={
                                "tags": obs.tags,
                            },
                            created_at=entity.created_at,
                            updated_at=_mtime_to_datetime(entity),
                            project_id=entity.project_id,
                        )
                    )

                for rel in entity.outgoing_relations:
                    relation_title = _strip_nul(
                        f"{rel.from_entity.title} -> {rel.to_entity.title}"
                        if rel.to_entity
                        else f"{rel.from_entity.title}"
                    )

                    rel_content_stems = _strip_nul(
                        "\n".join(
                            p for p in self._generate_variants(relation_title) if p and p.strip()
                        )
                    )
                    rows_to_index.append(
                        SearchIndexRow(
                            id=rel.id,
                            title=relation_title,
                            permalink=rel.permalink,
                            content_stems=rel_content_stems,
                            file_path=entity.file_path,
                            type=SearchItemType.RELATION.value,
                            entity_id=entity.id,
                            from_id=rel.from_id,
                            to_id=rel.to_id,
                            relation_type=rel.relation_type,
                            created_at=entity.created_at,
                            updated_at=_mtime_to_datetime(entity),
                            project_id=entity.project_id,
                        )
                    )

            with telemetry.scope(
                "search.index.bulk_upsert",
                phase="bulk_upsert",
                result_count=len(rows_to_index),
            ):
                await self.repository.bulk_index_items(rows_to_index)

    async def delete_by_permalink(self, permalink: str):
        """Delete an item from the search index."""
        await self.repository.delete_by_permalink(permalink)

    async def delete_by_entity_id(self, entity_id: int):
        """Delete an item from the search index."""
        await self.repository.delete_by_entity_id(entity_id)

    async def handle_delete(self, entity: Entity):
        """Handle complete entity deletion from search index including observations and relations.

        This replicates the logic from sync_service.handle_delete() to properly clean up
        all search index entries for an entity and its related data.
        """
        logger.debug(
            f"Cleaning up search index for entity_id={entity.id}, file_path={entity.file_path}, "
            f"observations={len(entity.observations)}, relations={len(entity.outgoing_relations)}"
        )

        # Clean up search index - same logic as sync_service.handle_delete()
        permalinks = (
            [entity.permalink]
            + [o.permalink for o in entity.observations]
            + [r.permalink for r in entity.outgoing_relations]
        )

        logger.debug(
            f"Deleting search index entries for entity_id={entity.id}, "
            f"index_entries={len(permalinks)}"
        )

        for permalink in permalinks:
            if permalink:
                await self.delete_by_permalink(permalink)
            else:
                await self.delete_by_entity_id(entity.id)
