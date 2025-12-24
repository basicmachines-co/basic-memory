"""PostgreSQL tsvector-based search repository implementation."""

import json
import re
from datetime import datetime
from typing import List, Optional


from loguru import logger
from sqlalchemy import text

from basic_memory import db
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.schemas.search import SearchItemType


class PostgresSearchRepository(SearchRepositoryBase):
    """PostgreSQL tsvector implementation of search repository.

    Uses PostgreSQL's full-text search capabilities with:
    - tsvector for document representation
    - tsquery for query representation
    - GIN indexes for performance
    - ts_rank() function for relevance scoring
    - JSONB containment operators for metadata search
    """

    async def init_search_index(self):
        """Create Postgres table with tsvector column and GIN indexes.

        Note: This is handled by Alembic migrations. This method is a no-op
        for Postgres as the schema is created via migrations.
        """
        logger.info("PostgreSQL search index initialization handled by migrations")
        # Table creation is done via Alembic migrations
        # This includes:
        # - CREATE TABLE search_index (...)
        # - ADD COLUMN textsearchable_index_col tsvector GENERATED ALWAYS AS (...)
        # - CREATE INDEX USING GIN on textsearchable_index_col
        # - CREATE INDEX USING GIN on metadata jsonb_path_ops
        pass

    def _prepare_search_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a search term for tsquery format.

        Args:
            term: The search term to prepare
            is_prefix: Whether to add prefix search capability (:* operator)

        Returns:
            Formatted search term for tsquery

        For Postgres:
        - Boolean operators are converted to tsquery format (&, |, !)
        - Prefix matching uses the :* operator
        - Terms are sanitized to prevent tsquery syntax errors
        """
        # Check for explicit boolean operators
        boolean_operators = [" AND ", " OR ", " NOT "]
        if any(op in f" {term} " for op in boolean_operators):
            return self._prepare_boolean_query(term)

        # For non-Boolean queries, prepare single term
        return self._prepare_single_term(term, is_prefix)

    def _prepare_boolean_query(self, query: str) -> str:
        """Convert Boolean query to tsquery format.

        Args:
            query: A Boolean query like "coffee AND brewing" or "(pour OR french) AND press"

        Returns:
            tsquery-formatted string with & (AND), | (OR), ! (NOT) operators

        Examples:
            "coffee AND brewing" -> "coffee & brewing"
            "(pour OR french) AND press" -> "(pour | french) & press"
            "coffee NOT decaf" -> "coffee & !decaf"
        """
        # Replace Boolean operators with tsquery operators
        # Keep parentheses for grouping
        result = query
        result = re.sub(r"\bAND\b", "&", result)
        result = re.sub(r"\bOR\b", "|", result)
        # NOT must be converted to "& !" and the ! must be attached to the following term
        # "Python NOT Django" -> "Python & !Django"
        result = re.sub(r"\bNOT\s+", "& !", result)

        return result

    def _prepare_single_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a single search term for tsquery.

        Args:
            term: A single search term
            is_prefix: Whether to add prefix search capability (:* suffix)

        Returns:
            A properly formatted single term for tsquery

        For Postgres tsquery:
        - Multi-word queries become "word1 & word2"
        - Prefix matching uses ":*" suffix (e.g., "coff:*")
        - Special characters that need escaping: & | ! ( ) :
        """
        if not term or not term.strip():
            return term

        term = term.strip()

        # Check if term is already a wildcard pattern
        if "*" in term:
            # Replace * with :* for Postgres prefix matching
            return term.replace("*", ":*")

        # Remove tsquery special characters from the search term
        # These characters have special meaning in tsquery and cause syntax errors
        # if not used as operators
        special_chars = ["&", "|", "!", "(", ")", ":"]
        cleaned_term = term
        for char in special_chars:
            cleaned_term = cleaned_term.replace(char, " ")

        # Handle multi-word queries
        if " " in cleaned_term:
            words = [w for w in cleaned_term.split() if w.strip()]
            if not words:
                # All characters were special chars, search won't match anything
                # Return a safe search term that won't cause syntax errors
                return "NOSPECIALCHARS:*"
            if is_prefix:
                # Add prefix matching to each word
                prepared_words = [f"{word}:*" for word in words]
            else:
                prepared_words = words
            # Join with AND operator
            return " & ".join(prepared_words)

        # Single word
        cleaned_term = cleaned_term.strip()
        if not cleaned_term:
            return "NOSPECIALCHARS:*"
        if is_prefix:
            return f"{cleaned_term}:*"
        else:
            return cleaned_term

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[SearchIndexRow]:
        """Search across all indexed content using PostgreSQL tsvector."""
        conditions = []
        params = {}
        order_by_clause = ""

        # Handle text search for title and content using tsvector
        if search_text:
            if search_text.strip() == "*" or search_text.strip() == "":
                # For wildcard searches, don't add any text conditions
                pass
            else:
                # Prepare search term for tsquery
                processed_text = self._prepare_search_term(search_text.strip())
                params["text"] = processed_text
                # Use @@ operator for tsvector matching
                conditions.append("textsearchable_index_col @@ to_tsquery('english', :text)")

        # Handle title search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            conditions.append("to_tsvector('english', title) @@ to_tsquery('english', :title_text)")

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("permalink = :permalink")

        # Handle permalink pattern match
        if permalink_match:
            permalink_text = permalink_match.lower().strip()
            params["permalink"] = permalink_text
            if "*" in permalink_match:
                # Use LIKE for pattern matching in Postgres
                # Convert * to % for SQL LIKE
                permalink_pattern = permalink_text.replace("*", "%")
                params["permalink"] = permalink_pattern
                conditions.append("permalink LIKE :permalink")
            else:
                conditions.append("permalink = :permalink")

        # Handle search item type filter
        if search_item_types:
            type_list = ", ".join(f"'{t.value}'" for t in search_item_types)
            conditions.append(f"type IN ({type_list})")

        # Handle entity type filter using JSONB containment
        if types:
            # Use JSONB @> operator for efficient containment queries
            type_conditions = []
            for entity_type in types:
                # Create JSONB containment condition for each type
                type_conditions.append(f'metadata @> \'{{"entity_type": "{entity_type}"}}\'')
            conditions.append(f"({' OR '.join(type_conditions)})")

        # Handle date filter
        if after_date:
            params["after_date"] = after_date
            conditions.append("created_at > :after_date")
            # order by most recent first
            order_by_clause = ", updated_at DESC"

        # Always filter by project_id
        params["project_id"] = self.project_id
        conditions.append("project_id = :project_id")

        # set limit and offset
        params["limit"] = limit
        params["offset"] = offset

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Build SQL with ts_rank() for scoring
        # Note: If no text search, score will be NULL, so we use COALESCE to default to 0
        if search_text and search_text.strip() and search_text.strip() != "*":
            score_expr = "ts_rank(textsearchable_index_col, to_tsquery('english', :text))"
        else:
            score_expr = "0"

        sql = f"""
            SELECT
                project_id,
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
                content_snippet,
                category,
                created_at,
                updated_at,
                {score_expr} as score
            FROM search_index
            WHERE {where_clause}
            ORDER BY score DESC, id ASC {order_by_clause}
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")
        try:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
        except Exception as e:
            # Handle tsquery syntax errors
            if "tsquery" in str(e).lower() or "syntax error" in str(e).lower():  # pragma: no cover
                logger.warning(f"tsquery syntax error for search term: {search_text}, error: {e}")
                # Return empty results rather than crashing
                return []
            else:
                # Re-raise other database errors
                logger.error(f"Database error during search: {e}")
                raise

        results = [
            SearchIndexRow(
                project_id=self.project_id,
                id=row.id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                type=row.type,
                score=float(row.score) if row.score else 0.0,
                metadata=(
                    row.metadata
                    if isinstance(row.metadata, dict)
                    else (json.loads(row.metadata) if row.metadata else {})
                ),
                from_id=row.from_id,
                to_id=row.to_id,
                relation_type=row.relation_type,
                entity_id=row.entity_id,
                content_snippet=row.content_snippet,
                category=row.category,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

        logger.trace(f"Found {len(results)} search results")
        for r in results:
            logger.trace(
                f"Search result: project_id: {r.project_id} type:{r.type} title: {r.title} permalink: {r.permalink} score: {r.score}"
            )

        return results

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple items in a single batch operation using UPSERT.

        Uses INSERT ... ON CONFLICT DO UPDATE to handle re-indexing of existing
        entities (e.g., during forward reference resolution) without requiring
        a separate delete operation. This eliminates race conditions between
        delete and insert operations in separate transactions.

        Args:
            search_index_rows: List of SearchIndexRow objects to index
        """

        if not search_index_rows:
            return

        async with db.scoped_session(self.session_maker) as session:
            # When using text() raw SQL, always serialize JSON to string
            # Both SQLite (TEXT) and Postgres (JSONB) accept JSON strings in raw SQL
            # The database driver/column type will handle conversion
            insert_data_list = []
            for row in search_index_rows:
                insert_data = row.to_insert(serialize_json=True)
                insert_data["project_id"] = self.project_id
                insert_data_list.append(insert_data)

            # Use UPSERT (INSERT ... ON CONFLICT) to handle re-indexing
            # Primary key is (id, type, project_id)
            # This handles race conditions during forward reference resolution
            # where an entity might be re-indexed before the delete commits
            # Syntax works for both SQLite 3.24+ and PostgreSQL
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type, :metadata,
                        :from_id, :to_id, :relation_type,
                        :entity_id, :category,
                        :created_at, :updated_at,
                        :project_id
                    )
                    ON CONFLICT (id, type, project_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content_stems = EXCLUDED.content_stems,
                        content_snippet = EXCLUDED.content_snippet,
                        permalink = EXCLUDED.permalink,
                        file_path = EXCLUDED.file_path,
                        metadata = EXCLUDED.metadata,
                        from_id = EXCLUDED.from_id,
                        to_id = EXCLUDED.to_id,
                        relation_type = EXCLUDED.relation_type,
                        entity_id = EXCLUDED.entity_id,
                        category = EXCLUDED.category,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                """),
                insert_data_list,
            )
            logger.debug(f"Bulk indexed {len(search_index_rows)} rows")
            await session.commit()
