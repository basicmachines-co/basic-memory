"""Service for search operations."""

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

        logger.debug(f"Searching with query: {query}")

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
            entity_types=query.entity_types,
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

        # Add trigrams for fuzzy matching
        variants.update(text[i : i + 3].lower() for i in range(len(text) - 2))

        return variants

    async def index_entity(
        self,
        entity: Entity,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> None:
        if background_tasks:
            background_tasks.add_task(self.index_entity_data, entity)
        else:
            await self.index_entity_data(entity)

    async def index_entity_data(
        self,
        entity: Entity,
    ) -> None:
        # delete all search index data associated with entity
        await self.repository.delete_by_entity_id(entity_id=entity.id)

        # reindex
        await self.index_entity_markdown(
            entity
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
                file_path=entity.file_path,
                metadata={
                    "entity_type": entity.entity_type,
                },
                created_at=entity.created_at,
                updated_at=entity.updated_at,
            )
        )

    async def index_entity_markdown(
        self,
        entity: Entity,
    ) -> None:
        """Index an entity and all its observations and relations.

        Indexing structure:
        1. Entities
           - permalink: direct from entity (e.g., "specs/search")
           - file_path: physical file location

        2. Observations
           - permalink: entity permalink + /observations/id (e.g., "specs/search/observations/123")
           - file_path: parent entity's file (where observation is defined)

        3. Relations (only index outgoing relations defined in this file)
           - permalink: from_entity/relation_type/to_entity (e.g., "specs/search/implements/features/search-ui")
           - file_path: source entity's file (where relation is defined)

        Each type gets its own row in the search index with appropriate metadata.
        """

        content_stems = []
        content_snippet = ""
        title_variants = self._generate_variants(entity.title)
        content_stems.extend(title_variants)

        content = await self.file_service.read_entity_content(entity)
        if content:
            content_stems.append(content)
            content_snippet = f"{content[:250]}"

        if entity.permalink:
            content_stems.extend(self._generate_variants(entity.permalink))

        content_stems.extend(self._generate_variants(entity.file_path))

        entity_content_stems = "\n".join(p for p in content_stems if p and p.strip())

        # Index entity
        await self.repository.index_item(
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
                updated_at=entity.updated_at,
            )
        )

        # Index each observation with permalink
        for obs in entity.observations:
            # Index with parent entity's file path since that's where it's defined
            obs_content_stems = "\n".join(
                p for p in self._generate_variants(obs.content) if p and p.strip()
            )
            await self.repository.index_item(
                SearchIndexRow(
                    id=obs.id,
                    type=SearchItemType.OBSERVATION.value,
                    title=f"{obs.category}: {obs.content[:100]}...",
                    content_stems=obs_content_stems,
                    content_snippet=obs.content,
                    permalink=obs.permalink,
                    file_path=entity.file_path,
                    category=obs.category,
                    entity_id=entity.id,
                    metadata={
                        "tags": obs.tags,
                    },
                    created_at=entity.created_at,
                    updated_at=entity.updated_at,
                )
            )

        # Only index outgoing relations (ones defined in this file)
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
            await self.repository.index_item(
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
                    updated_at=entity.updated_at,
                )
            )

    async def delete_by_permalink(self, permalink: str):
        """Delete an item from the search index."""
        await self.repository.delete_by_permalink(permalink)

    async def delete_by_entity_id(self, entity_id: int):
        """Delete an item from the search index."""
        await self.repository.delete_by_entity_id(entity_id)
