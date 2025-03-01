"""Service for building rich context from the knowledge graph."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import text

from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.schemas.memory import MemoryUrl, memory_url_path
from basic_memory.schemas.search import SearchItemType


@dataclass
class ContextResultRow:
    type: str
    id: int
    title: str
    permalink: str
    file_path: str
    depth: int
    root_id: int
    created_at: datetime
    from_id: Optional[int] = None
    to_id: Optional[int] = None
    relation_type: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    entity_id: Optional[int] = None


class ContextService:
    """Service for building rich context from memory:// URIs.

    Handles three types of context building:
    1. Direct permalink lookup - exact match on path
    2. Pattern matching - using * wildcards
    3. Special modes via params (e.g., 'related')
    """

    def __init__(
        self,
        search_repository: SearchRepository,
        entity_repository: EntityRepository,
    ):
        self.search_repository = search_repository
        self.entity_repository = entity_repository

    async def build_context(
        self,
        memory_url: Optional[MemoryUrl] = None,
        types: Optional[List[SearchItemType]] = None,
        depth: int = 1,
        since: Optional[datetime] = None,
        limit=10,
        offset=0,
        max_related: int = 10,
    ):
        """Build rich context from a memory:// URI."""
        logger.debug(
            f"Building context for URI: '{memory_url}' depth: '{depth}' since: '{since}' limit: '{limit}' offset: '{offset}'  max_related: '{max_related}'"
        )

        if memory_url:
            path = memory_url_path(memory_url)
            # Pattern matching - use search
            if "*" in path:
                logger.debug(f"Pattern search for '{path}'")
                primary = await self.search_repository.search(
                    permalink_match=path, limit=limit, offset=offset
                )

            # Direct lookup for exact path
            else:
                logger.debug(f"Direct lookup for '{path}'")
                primary = await self.search_repository.search(
                    permalink=path, limit=limit, offset=offset
                )
        else:
            logger.debug(f"Build context for '{types}'")
            primary = await self.search_repository.search(
                types=types, after_date=since, limit=limit, offset=offset
            )

        # Get type_id pairs for traversal

        type_id_pairs = [(r.type, r.id) for r in primary] if primary else []
        logger.debug(f"found primary type_id_pairs: {len(type_id_pairs)}")

        # Find related content
        related = await self.find_related(
            type_id_pairs, max_depth=depth, since=since, max_results=max_related
        )
        logger.debug(f"Found {len(related)} related results")
        for r in related:
            logger.debug(f"Found related {r.type}: {r.permalink}")

        # Build response
        return {
            "primary_results": primary,
            "related_results": related,
            "metadata": {
                "uri": memory_url_path(memory_url) if memory_url else None,
                "types": types if types else None,
                "depth": depth,
                "timeframe": since.isoformat() if since else None,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "matched_results": len(primary),
                "total_results": len(primary) + len(related),
                "total_relations": sum(1 for r in related if r.type == SearchItemType.RELATION),
            },
        }

    async def find_related(
        self,
        type_id_pairs: List[Tuple[str, int]],
        max_depth: int = 1,
        since: Optional[datetime] = None,
        max_results: int = 10,
    ) -> List[ContextResultRow]:
        """Find items connected through relations.

        Uses recursive CTE to find:
        - Connected entities
        - Their observations
        - Relations that connect them

        Note on depth:
        Each traversal step requires two depth levels - one to find the relation,
        and another to follow that relation to an entity. So a max_depth of 4 allows
        traversal through two entities (relation->entity->relation->entity), while reaching
        an entity three steps away requires max_depth=6 (relation->entity->relation->entity->relation->entity).
        """
        max_depth = max_depth * 2

        if not type_id_pairs:
            return []

        logger.debug(f"Finding connected items for {type_id_pairs} with depth {max_depth}")

        # Build the VALUES clause directly since SQLite doesn't handle parameterized IN well
        values = ", ".join([f"('{t}', {i})" for t, i in type_id_pairs])

        # Parameters for bindings
        params = {"max_depth": max_depth, "max_results": max_results}
        if since:
            params["since_date"] = since.isoformat()  # pyright: ignore

        # Build date filter
        date_filter = "AND base.created_at >= :since_date" if since else ""
        r1_date_filter = "AND r.created_at >= :since_date" if since else ""
        related_date_filter = "AND e.created_at >= :since_date" if since else ""

        query = text(f"""
        WITH RECURSIVE context_graph AS (
            -- Base case: seed items 
            SELECT 
                id,
                type,
                title, 
                permalink,
                file_path,
                from_id,
                to_id,
                relation_type,
                content_snippet as content,
                category,
                entity_id,
                0 as depth,
                id as root_id,
                created_at,
                created_at as relation_date,
                0 as is_incoming
            FROM search_index base
            WHERE (base.type, base.id) IN ({values})
            {date_filter}

            UNION ALL  -- Allow same paths at different depths

            -- Get relations from current entities 
            SELECT DISTINCT
                r.id,
                r.type,
                r.title,
                r.permalink,
                r.file_path,
                r.from_id,
                r.to_id,
                r.relation_type,
                r.content_snippet as content,
                r.category,
                r.entity_id,
                cg.depth + 1,
                cg.root_id,
                r.created_at,
                r.created_at as relation_date,
                CASE WHEN r.from_id = cg.id THEN 0 ELSE 1 END as is_incoming
            FROM context_graph cg
            JOIN search_index r ON (
                cg.type = 'entity' AND
                r.type = 'relation' AND 
                (r.from_id = cg.id OR r.to_id = cg.id)
                {r1_date_filter}
            )
            WHERE cg.depth < :max_depth

            UNION ALL

            -- Get entities connected by relations
            SELECT DISTINCT
                e.id,
                e.type,
                e.title,
                e.permalink,
                e.file_path,
                e.from_id,
                e.to_id,
                e.relation_type,
                e.content_snippet as content,
                e.category,
                e.entity_id,
                cg.depth + 1,  -- Increment depth for entities
                cg.root_id,
                e.created_at,
                cg.relation_date,
                cg.is_incoming
            FROM context_graph cg
            JOIN search_index e ON (
                cg.type = 'relation' AND 
                e.type = 'entity' AND
                e.id = CASE 
                    WHEN cg.is_incoming = 0 THEN cg.to_id  -- Fixed entity lookup
                    ELSE cg.from_id                             
                END
                {related_date_filter}
            )
            WHERE cg.depth < :max_depth
        )
        SELECT DISTINCT 
            type,
            id,
            title,
            permalink,
            file_path,
            from_id,
            to_id,
            relation_type,
            content,
            category,
            entity_id,
            MIN(depth) as depth,
            root_id,
            created_at
        FROM context_graph
        WHERE (type, id) NOT IN ({values})
        GROUP BY
            type, id, title, permalink, from_id, to_id,
            relation_type, category, entity_id,
            root_id, created_at
        ORDER BY depth, type, id
        LIMIT :max_results
       """)

        result = await self.search_repository.execute_query(query, params=params)
        rows = result.all()

        context_rows = [
            ContextResultRow(
                type=row.type,
                id=row.id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                from_id=row.from_id,
                to_id=row.to_id,
                relation_type=row.relation_type,
                content=row.content,
                category=row.category,
                entity_id=row.entity_id,
                depth=row.depth,
                root_id=row.root_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
        return context_rows
