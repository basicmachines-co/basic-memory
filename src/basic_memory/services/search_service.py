"""Service for search operations."""

import ast
from datetime import datetime
from typing import List, Optional, Set


from dateparser import parse
from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy import text

from basic_memory.models import Entity
from basic_memory.repository import EntityRepository
from basic_memory.repository.search_repository import SearchRepository, SearchIndexRow
from basic_memory.schemas.search import SearchQuery, SearchItemType
from basic_memory.services import FileService

# Maximum size for content_stems field to stay under Postgres's 8KB index row limit.
# We use 6000 characters to leave headroom for other indexed columns and overhead.
MAX_CONTENT_STEMS_SIZE = 6000


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
        if query.no_criteria():
            logger.debug("no criteria passed to query")
            return []

        logger.trace(f"Searching with query: {query}")

        after_date = (
            (
                query.after_date
                if isinstance(query.after_date, datetime)
                else parse(query.after_date)
            )
            if query.after_date
            else None
        )

        # search
        results = await self.repository.search(
            search_text=query.text,
            permalink=query.permalink,
            permalink_match=query.permalink_match,
            title=query.title,
            types=query.types,
            search_item_types=query.entity_types,
            after_date=after_date,
            limit=limit,
            offset=offset,
        )

        return results

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
        # delete all search index data associated with entity
        await self.repository.delete_by_entity_id(entity_id=entity.id)

        # reindex
        await self.index_entity_markdown(
            entity, content
        ) if entity.is_markdown else await self.index_entity_file(entity)

    async def index_entity_file(
        self,
        entity: Entity,
    ) -> None:
        # Index entity file with no content
        await self.repository.index_item(
            SearchIndexRow(
                id=entity.id,
                entity_id=entity.id,
                type=SearchItemType.ENTITY.value,
                title=entity.title,
                permalink=entity.permalink,  # Required for Postgres NOT NULL constraint
                file_path=entity.file_path,
                metadata={
                    "entity_type": entity.entity_type,
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

        # Collect all search index rows to batch insert at the end
        rows_to_index = []

        content_stems = []
        content_snippet = ""
        title_variants = self._generate_variants(entity.title)
        content_stems.extend(title_variants)

        # Use provided content or read from file
        if content is None:
            content = await self.file_service.read_entity_content(entity)
        if content:
            content_stems.append(content)
            content_snippet = f"{content[:250]}"

        if entity.permalink:
            content_stems.extend(self._generate_variants(entity.permalink))

        content_stems.extend(self._generate_variants(entity.file_path))

        # Add entity tags from frontmatter to search content
        entity_tags = self._extract_entity_tags(entity)
        if entity_tags:
            content_stems.extend(entity_tags)

        entity_content_stems = "\n".join(p for p in content_stems if p and p.strip())

        # Truncate to stay under Postgres's 8KB index row limit
        if len(entity_content_stems) > MAX_CONTENT_STEMS_SIZE:
            entity_content_stems = entity_content_stems[:MAX_CONTENT_STEMS_SIZE]

        # Add entity row
        rows_to_index.append(
            SearchIndexRow(
                id=entity.id,
                type=SearchItemType.ENTITY.value,
                title=entity.title,
                content_stems=entity_content_stems,
                content_snippet=content_snippet,
                permalink=entity.permalink,
                file_path=entity.file_path,
                entity_id=entity.id,
                metadata={
                    "entity_type": entity.entity_type,
                },
                created_at=entity.created_at,
                updated_at=_mtime_to_datetime(entity),
                project_id=entity.project_id,
            )
        )

        # Add observation rows - dedupe by permalink to avoid unique constraint violations
        # Two observations with same entity/category/content generate identical permalinks
        seen_permalinks: set[str] = {entity.permalink} if entity.permalink else set()
        for obs in entity.observations:
            obs_permalink = obs.permalink
            if obs_permalink in seen_permalinks:
                logger.debug(f"Skipping duplicate observation permalink: {obs_permalink}")
                continue
            seen_permalinks.add(obs_permalink)

            # Index with parent entity's file path since that's where it's defined
            obs_content_stems = "\n".join(
                p for p in self._generate_variants(obs.content) if p and p.strip()
            )
            # Truncate to stay under Postgres's 8KB index row limit
            if len(obs_content_stems) > MAX_CONTENT_STEMS_SIZE:
                obs_content_stems = obs_content_stems[:MAX_CONTENT_STEMS_SIZE]
            rows_to_index.append(
                SearchIndexRow(
                    id=obs.id,
                    type=SearchItemType.OBSERVATION.value,
                    title=f"{obs.category}: {obs.content[:100]}...",
                    content_stems=obs_content_stems,
                    content_snippet=obs.content,
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

        # Add relation rows (only outgoing relations defined in this file)
        for rel in entity.outgoing_relations:
            # Create descriptive title showing the relationship
            relation_title = (
                f"{rel.from_entity.title} → {rel.to_entity.title}"
                if rel.to_entity
                else f"{rel.from_entity.title}"
            )

            rel_content_stems = "\n".join(
                p for p in self._generate_variants(relation_title) if p and p.strip()
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

        # Batch insert all rows at once
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
