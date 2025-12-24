"""Service for building rich context from the knowledge graph."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple


from loguru import logger
from sqlalchemy import text

from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_repository import SearchRepository, SearchIndexRow
from basic_memory.schemas.memory import MemoryUrl, memory_url_path
from basic_memory.schemas.search import SearchItemType
from basic_memory.utils import generate_permalink


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


@dataclass
class ContextResultItem:
    """A hierarchical result containing a primary item with its observations and related items."""

    primary_result: ContextResultRow | SearchIndexRow
    observations: List[ContextResultRow] = field(default_factory=list)
    related_results: List[ContextResultRow] = field(default_factory=list)


@dataclass
class ContextMetadata:
    """Metadata about a context result."""

    uri: Optional[str] = None
    types: Optional[List[SearchItemType]] = None
    depth: int = 1
    timeframe: Optional[str] = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    primary_count: int = 0
    related_count: int = 0
    total_observations: int = 0
    total_relations: int = 0


@dataclass
class ContextResult:
    """Complete context result with metadata."""

    results: List[ContextResultItem] = field(default_factory=list)
    metadata: ContextMetadata = field(default_factory=ContextMetadata)


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
        observation_repository: ObservationRepository,
    ):
        self.search_repository = search_repository
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository

    async def build_context(
        self,
        memory_url: Optional[MemoryUrl] = None,
        types: Optional[List[SearchItemType]] = None,
        depth: int = 1,
        since: Optional[datetime] = None,
        limit=10,
        offset=0,
        max_related: int = 10,
        include_observations: bool = True,
    ) -> ContextResult:
        """Build rich context from a memory:// URI."""
        logger.debug(
            f"Building context for URI: '{memory_url}' depth: '{depth}' since: '{since}' limit: '{limit}' offset: '{offset}'  max_related: '{max_related}'"
        )

        normalized_path: Optional[str] = None
        if memory_url:
            path = memory_url_path(memory_url)
            # Check for wildcards before normalization
            has_wildcard = "*" in path

            if has_wildcard:
                # For wildcard patterns, normalize each segment separately to preserve the *
                parts = path.split("*")
                normalized_parts = [
                    generate_permalink(part, split_extension=False) if part else ""
                    for part in parts
                ]
                normalized_path = "*".join(normalized_parts)
                logger.debug(f"Pattern search for '{normalized_path}'")
                primary = await self.search_repository.search(
                    permalink_match=normalized_path, limit=limit, offset=offset
                )
            else:
                # For exact paths, normalize the whole thing
                normalized_path = generate_permalink(path, split_extension=False)
                logger.debug(f"Direct lookup for '{normalized_path}'")
                primary = await self.search_repository.search(
                    permalink=normalized_path, limit=limit, offset=offset
                )
        else:
            logger.debug(f"Build context for '{types}'")
            primary = await self.search_repository.search(
                search_item_types=types, after_date=since, limit=limit, offset=offset
            )

        # Get type_id pairs for traversal

        type_id_pairs = [(r.type, r.id) for r in primary] if primary else []
        logger.debug(f"found primary type_id_pairs: {len(type_id_pairs)}")

        # Find related content
        related = await self.find_related(
            type_id_pairs, max_depth=depth, since=since, max_results=max_related
        )
        logger.debug(f"Found {len(related)} related results")

        # Collect entity IDs from primary and related results
        entity_ids = []
        for result in primary:
            if result.type == SearchItemType.ENTITY.value:
                entity_ids.append(result.id)

        for result in related:
            if result.type == SearchItemType.ENTITY.value:
                entity_ids.append(result.id)

        # Fetch observations for all entities if requested
        observations_by_entity = {}
        if include_observations and entity_ids:
            # Use our observation repository to get observations for all entities at once
            observations_by_entity = await self.observation_repository.find_by_entities(entity_ids)
            logger.debug(f"Found observations for {len(observations_by_entity)} entities")

        # Create metadata dataclass
        metadata = ContextMetadata(
            uri=normalized_path if memory_url else None,
            types=types,
            depth=depth,
            timeframe=since.isoformat() if since else None,
            primary_count=len(primary),
            related_count=len(related),
            total_observations=sum(len(obs) for obs in observations_by_entity.values()),
            total_relations=sum(1 for r in related if r.type == SearchItemType.RELATION),
        )

        # Build context results list directly with ContextResultItem objects
        context_results = []

        # For each primary result
        for primary_item in primary:
            # Find all related items with this primary item as root
            related_to_primary = [r for r in related if r.root_id == primary_item.id]

            # Get observations for this item if it's an entity
            item_observations = []
            if primary_item.type == SearchItemType.ENTITY.value and include_observations:
                # Convert Observation models to ContextResultRows
                for obs in observations_by_entity.get(primary_item.id, []):
                    item_observations.append(
                        ContextResultRow(
                            type="observation",
                            id=obs.id,
                            title=f"{obs.category}: {obs.content[:50]}...",
                            permalink=generate_permalink(
                                f"{primary_item.permalink}/observations/{obs.category}/{obs.content}"
                            ),
                            file_path=primary_item.file_path,
                            content=obs.content,
                            category=obs.category,
                            entity_id=primary_item.id,
                            depth=0,
                            root_id=primary_item.id,
                            created_at=primary_item.created_at,  # created_at time from entity
                        )
                    )

            # Create ContextResultItem directly
            context_item = ContextResultItem(
                primary_result=primary_item,
                observations=item_observations,
                related_results=related_to_primary,
            )

            context_results.append(context_item)

        # Return the structured ContextResult
        return ContextResult(results=context_results, metadata=metadata)

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

        # Extract entity IDs from type_id_pairs for the optimized query
        entity_ids = [i for t, i in type_id_pairs if t == "entity"]

        if not entity_ids:
            logger.debug("No entity IDs found in type_id_pairs")
            return []

        logger.debug(
            f"Finding connected items for {len(entity_ids)} entities with depth {max_depth}"
        )

        # Build the VALUES clause for entity IDs
        entity_id_values = ", ".join([str(i) for i in entity_ids])

        # Parameters for bindings - include project_id for security filtering
        params = {
            "max_depth": max_depth,
            "max_results": max_results,
            "project_id": self.search_repository.project_id,
        }

        # Build date and timeframe filters conditionally based on since parameter
        if since:
            # SQLite accepts ISO strings, but Postgres/asyncpg requires datetime objects
            if isinstance(self.search_repository, PostgresSearchRepository):
                # asyncpg expects timezone-NAIVE datetime in UTC for DateTime(timezone=True) columns
                # even though the column stores timezone-aware values
                since_utc = since.astimezone(timezone.utc) if since.tzinfo else since
                params["since_date"] = since_utc.replace(tzinfo=None)  # pyright: ignore
            else:
                params["since_date"] = since.isoformat()  # pyright: ignore
            date_filter = "AND e.created_at >= :since_date"
            relation_date_filter = "AND e_from.created_at >= :since_date"
            timeframe_condition = "AND eg.relation_date >= :since_date"
        else:
            date_filter = ""
            relation_date_filter = ""
            timeframe_condition = ""

        # Add project filtering for security - ensure all entities and relations belong to the same project
        project_filter = "AND e.project_id = :project_id"
        relation_project_filter = "AND e_from.project_id = :project_id"

        # Use a CTE that operates directly on entity and relation tables
        # This avoids the overhead of the search_index virtual table
        # Note: Postgres and SQLite have different CTE limitations:
        # - Postgres: doesn't allow multiple UNION ALL branches referencing the CTE
        # - SQLite: doesn't support LATERAL joins
        # So we need different queries for each database backend

        # Detect database backend
        is_postgres = isinstance(self.search_repository, PostgresSearchRepository)

        if is_postgres:
            query = self._build_postgres_query(
                entity_id_values,
                date_filter,
                project_filter,
                relation_date_filter,
                relation_project_filter,
                timeframe_condition,
            )
        else:
            # SQLite needs VALUES clause for exclusion (not needed for Postgres)
            values = ", ".join([f"('{t}', {i})" for t, i in type_id_pairs])
            query = self._build_sqlite_query(
                entity_id_values,
                date_filter,
                project_filter,
                relation_date_filter,
                relation_project_filter,
                timeframe_condition,
                values,
            )

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

    def _build_postgres_query(
        self,
        entity_id_values: str,
        date_filter: str,
        project_filter: str,
        relation_date_filter: str,
        relation_project_filter: str,
        timeframe_condition: str,
    ):
        """Build Postgres-specific CTE query using LATERAL joins."""
        return text(f"""
        WITH RECURSIVE entity_graph AS (
            -- Base case: seed entities
            SELECT
                e.id,
                'entity' as type,
                e.title,
                e.permalink,
                e.file_path,
                CAST(NULL AS INTEGER) as from_id,
                CAST(NULL AS INTEGER) as to_id,
                CAST(NULL AS TEXT) as relation_type,
                CAST(NULL AS TEXT) as content,
                CAST(NULL AS TEXT) as category,
                CAST(NULL AS INTEGER) as entity_id,
                0 as depth,
                e.id as root_id,
                e.created_at,
                e.created_at as relation_date
            FROM entity e
            WHERE e.id IN ({entity_id_values})
            {date_filter}
            {project_filter}

            UNION ALL

            -- Fetch BOTH relations AND connected entities in a single recursive step
            -- Postgres only allows ONE reference to the recursive CTE in the recursive term
            -- We use CROSS JOIN LATERAL to generate two rows (relation + entity) from each traversal
            SELECT
                CASE
                    WHEN step_type = 1 THEN r.id
                    ELSE e.id
                END as id,
                CASE
                    WHEN step_type = 1 THEN 'relation'
                    ELSE 'entity'
                END as type,
                CASE
                    WHEN step_type = 1 THEN r.relation_type || ': ' || r.to_name
                    ELSE e.title
                END as title,
                CASE
                    WHEN step_type = 1 THEN ''
                    ELSE COALESCE(e.permalink, '')
                END as permalink,
                CASE
                    WHEN step_type = 1 THEN e_from.file_path
                    ELSE e.file_path
                END as file_path,
                CASE
                    WHEN step_type = 1 THEN r.from_id
                    ELSE NULL
                END as from_id,
                CASE
                    WHEN step_type = 1 THEN r.to_id
                    ELSE NULL
                END as to_id,
                CASE
                    WHEN step_type = 1 THEN r.relation_type
                    ELSE NULL
                END as relation_type,
                CAST(NULL AS TEXT) as content,
                CAST(NULL AS TEXT) as category,
                CAST(NULL AS INTEGER) as entity_id,
                eg.depth + step_type as depth,
                eg.root_id,
                CASE
                    WHEN step_type = 1 THEN e_from.created_at
                    ELSE e.created_at
                END as created_at,
                CASE
                    WHEN step_type = 1 THEN e_from.created_at
                    ELSE eg.relation_date
                END as relation_date
            FROM entity_graph eg
            CROSS JOIN LATERAL (VALUES (1), (2)) AS steps(step_type)
            JOIN relation r ON (
                eg.type = 'entity' AND
                (r.from_id = eg.id OR r.to_id = eg.id)
            )
            JOIN entity e_from ON (
                r.from_id = e_from.id
                {relation_project_filter}
            )
            LEFT JOIN entity e ON (
                step_type = 2 AND
                e.id = CASE
                    WHEN r.from_id = eg.id THEN r.to_id
                    ELSE r.from_id
                END
                {date_filter}
                {project_filter}
            )
            WHERE eg.depth < :max_depth
            AND (step_type = 1 OR (step_type = 2 AND e.id IS NOT NULL AND e.id != eg.id))
            {timeframe_condition}
        )
        -- Materialize and filter
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
        FROM entity_graph
        WHERE depth > 0
        GROUP BY type, id, title, permalink, file_path, from_id, to_id,
                 relation_type, content, category, entity_id, root_id, created_at
        ORDER BY depth, type, id
        LIMIT :max_results
       """)

    def _build_sqlite_query(
        self,
        entity_id_values: str,
        date_filter: str,
        project_filter: str,
        relation_date_filter: str,
        relation_project_filter: str,
        timeframe_condition: str,
        values: str,
    ):
        """Build SQLite-specific CTE query using multiple UNION ALL branches."""
        return text(f"""
        WITH RECURSIVE entity_graph AS (
            -- Base case: seed entities
            SELECT
                e.id,
                'entity' as type,
                e.title,
                e.permalink,
                e.file_path,
                NULL as from_id,
                NULL as to_id,
                NULL as relation_type,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                0 as depth,
                e.id as root_id,
                e.created_at,
                e.created_at as relation_date,
                0 as is_incoming
            FROM entity e
            WHERE e.id IN ({entity_id_values})
            {date_filter}
            {project_filter}

            UNION ALL

            -- Get relations from current entities
            SELECT
                r.id,
                'relation' as type,
                r.relation_type || ': ' || r.to_name as title,
                '' as permalink,
                e_from.file_path,
                r.from_id,
                r.to_id,
                r.relation_type,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                eg.depth + 1,
                eg.root_id,
                e_from.created_at,
                e_from.created_at as relation_date,
                CASE WHEN r.from_id = eg.id THEN 0 ELSE 1 END as is_incoming
            FROM entity_graph eg
            JOIN relation r ON (
                eg.type = 'entity' AND
                (r.from_id = eg.id OR r.to_id = eg.id)
            )
            JOIN entity e_from ON (
                r.from_id = e_from.id
                {relation_date_filter}
                {relation_project_filter}
            )
            LEFT JOIN entity e_to ON (r.to_id = e_to.id)
            WHERE eg.depth < :max_depth
            AND (r.to_id IS NULL OR e_to.project_id = :project_id)

            UNION ALL

            -- Get entities connected by relations
            SELECT
                e.id,
                'entity' as type,
                e.title,
                CASE
                    WHEN e.permalink IS NULL THEN ''
                    ELSE e.permalink
                END as permalink,
                e.file_path,
                NULL as from_id,
                NULL as to_id,
                NULL as relation_type,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                eg.depth + 1,
                eg.root_id,
                e.created_at,
                eg.relation_date,
                eg.is_incoming
            FROM entity_graph eg
            JOIN entity e ON (
                eg.type = 'relation' AND
                e.id = CASE
                    WHEN eg.is_incoming = 0 THEN eg.to_id
                    ELSE eg.from_id
                END
                {date_filter}
                {project_filter}
            )
            WHERE eg.depth < :max_depth
            {timeframe_condition}
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
        FROM entity_graph
        WHERE depth > 0
        GROUP BY type, id, title, permalink, file_path, from_id, to_id,
                 relation_type, content, category, entity_id, root_id, created_at
        ORDER BY depth, type, id
        LIMIT :max_results
       """)
