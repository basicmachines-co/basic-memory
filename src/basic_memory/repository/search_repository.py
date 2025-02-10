"""Repository for search operations."""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Any, Dict

from loguru import logger
from sqlalchemy import text, Executable, Result
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models.search import CREATE_SEARCH_INDEX
from basic_memory.schemas.search import SearchItemType


@dataclass
class SearchIndexRow:
    """Search result with score and metadata."""

    id: int
    type: str
    permalink: str
    file_path: str 
    metadata: Optional[dict] = None

    # date values
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # assigned in result
    score: Optional[float] = None

    # Type-specific fields
    title: Optional[str] = None  # entity
    content: Optional[str] = None  # entity, observation
    entity_id: Optional[int] = None  # observations
    category: Optional[str] = None  # observations
    from_id: Optional[int] = None  # relations
    to_id: Optional[int] = None  # relations
    relation_type: Optional[str] = None  # relations

    def to_insert(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "permalink": self.permalink,
            "file_path": self.file_path,
            "type": self.type,
            "metadata": json.dumps(self.metadata),
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation_type": self.relation_type,
            "entity_id": self.entity_id,
            "category": self.category,
            "created_at": self.created_at if self.created_at else None,
            "updated_at": self.updated_at if self.updated_at else None,
        }


class SearchRepository:
    """Repository for search index operations."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]):
        self.session_maker = session_maker

    async def init_search_index(self):
        """Create or recreate the search index."""
        
        logger.info("Initializing search index")
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(CREATE_SEARCH_INDEX)
            await session.commit()

    def _quote_search_term(self, term: str) -> str:
        """Add quotes if term contains special characters.
        For FTS5, special characters and phrases need to be quoted to be treated as a single token.
        """
        # List of special characters that need quoting
        special_chars = ['/', '*', '-', '.', ' ', '(', ')', '[', ']', '"', "'"]
        
        # Check if term contains any special characters
        if any(c in term for c in special_chars):
            # If the term already contains quotes, escape them
            term = term.replace('"', '""')
            return f'"{term}"'
        return term

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        types: Optional[List[SearchItemType]] = None,
        after_date: Optional[datetime] = None,
        entity_types: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[SearchIndexRow]:
        """Search across all indexed content with fuzzy matching."""
        conditions = []
        params = {}
        order_by_clause = ""
        
        # Handle text search for title and content
        if search_text:
            search_text = self._quote_search_term(search_text.lower().strip())
            params["text"] = f"{search_text}*"
            conditions.append("(title MATCH :text OR content MATCH :text)")

        # Handle title match search
        if title:
            title_text = self._quote_search_term(title.lower().strip())
            params["text"] = f"{title_text}*"
            conditions.append("title MATCH :text")

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("permalink = :permalink")

        # Handle permalink match search, supports *
        if permalink_match:
            params["permalink"] = self._quote_search_term(permalink_match)
            conditions.append("permalink MATCH :permalink")
            
        # Handle type filter
        if types:
            type_list = ", ".join(f"'{t.value}'" for t in types)
            conditions.append(f"type IN ({type_list})")

        # Handle entity type filter
        if entity_types:
            entity_type_list = ", ".join(f"'{t}'" for t in entity_types)
            conditions.append(f"json_extract(metadata, '$.entity_type') IN ({entity_type_list})")

        # Handle date filter using datetime() for proper comparison
        if after_date:
            params["after_date"] = after_date
            conditions.append("datetime(created_at) > datetime(:after_date)")
            
            # order by most recent first
            order_by_clause = ", updated_at DESC"

        # set limit on search query
        params["limit"] = limit
        
        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
            SELECT 
                id, 
                title, 
                permalink,
                file_path,
                type,
                metadata,
                from_id,
                to_id,
                relation_type,
                entity_id,
                content,
                category,
                created_at,
                updated_at,
                bm25(search_index) as score
            FROM search_index 
            WHERE {where_clause}
            ORDER BY score ASC {order_by_clause}
            LIMIT :limit
        """

        #logger.debug(f"Search {sql} params: {params}")
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()

        results = [
            SearchIndexRow(
                id=row.id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                type=row.type,
                score=row.score,
                metadata=json.loads(row.metadata),
                from_id=row.from_id,
                to_id=row.to_id,
                relation_type=row.relation_type,
                entity_id=row.entity_id,
                content=row.content,
                category=row.category,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

        #for r in results:
        #    logger.debug(f"Search result: type:{r.type} title: {r.title} permalink: {r.permalink} score: {r.score}")
        
        return results

    async def index_item(
        self,
        search_index_row: SearchIndexRow,
    ):
        """Index or update a single item."""
        async with db.scoped_session(self.session_maker) as session:
            # Delete existing record if any
            await session.execute(
                text("DELETE FROM search_index WHERE permalink = :permalink"),
                {"permalink": search_index_row.permalink},
            )

            # Insert new record
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at
                    ) VALUES (
                        :id, :title, :content, :permalink, :file_path, :type, :metadata,
                        :from_id, :to_id, :relation_type,
                        :entity_id, :category,
                        :created_at, :updated_at
                    )
                """),
                search_index_row.to_insert(),
            )
            logger.debug(f"indexed permalink {search_index_row.permalink}")
            await session.commit()

    async def delete_by_permalink(self, permalink: str):
        """Delete an item from the search index."""
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text("DELETE FROM search_index WHERE permalink = :permalink"),
                {"permalink": permalink},
            )
            await session.commit()

    async def execute_query(
        self,
        query: Executable,
        params: Optional[Dict[str, Any]] = None,
    ) -> Result[Any]:
        """Execute a query asynchronously."""
        #logger.debug(f"Executing query: {query}")
        async with db.scoped_session(self.session_maker) as session:
            start_time = time.perf_counter()
            if params:
                result = await session.execute(query, params)
            else:
                result = await session.execute(query)
            end_time = time.perf_counter()
            elapsed_time = end_time - start_time
            logger.debug(f"Query executed successfully in {elapsed_time:.2f}s.")
            return result