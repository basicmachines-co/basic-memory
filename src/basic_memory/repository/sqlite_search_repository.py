"""SQLite FTS5-based search repository implementation."""

import json
import re
from datetime import datetime
from typing import List, Optional


from loguru import logger
from sqlalchemy import text

from basic_memory import db
from basic_memory.models.search import CREATE_SEARCH_INDEX
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.schemas.search import SearchItemType


class SQLiteSearchRepository(SearchRepositoryBase):
    """SQLite FTS5 implementation of search repository.

    Uses SQLite's FTS5 virtual tables for full-text search with:
    - MATCH operator for queries
    - bm25() function for relevance scoring
    - Special character quoting for syntax safety
    - Prefix wildcard matching with *
    """

    async def init_search_index(self):
        """Create FTS5 virtual table for search.

        Note: Drops any existing search_index table first to ensure FTS5 virtual table creation.
        This is necessary because Base.metadata.create_all() might create a regular table.
        """
        logger.info("Initializing SQLite FTS5 search index")
        try:
            async with db.scoped_session(self.session_maker) as session:
                # Drop any existing regular or virtual table first
                await session.execute(text("DROP TABLE IF EXISTS search_index"))
                # Create FTS5 virtual table
                await session.execute(CREATE_SEARCH_INDEX)
                await session.commit()
        except Exception as e:  # pragma: no cover
            logger.error(f"Error initializing search index: {e}")
            raise e

    def _prepare_boolean_query(self, query: str) -> str:
        """Prepare a Boolean query by quoting individual terms while preserving operators.

        Args:
            query: A Boolean query like "tier1-test AND unicode" or "(hello OR world) NOT test"

        Returns:
            A properly formatted Boolean query with quoted terms that need quoting
        """
        # Define Boolean operators and their boundaries
        boolean_pattern = r"(\bAND\b|\bOR\b|\bNOT\b)"

        # Split the query by Boolean operators, keeping the operators
        parts = re.split(boolean_pattern, query)

        processed_parts = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # If it's a Boolean operator, keep it as is
            if part in ["AND", "OR", "NOT"]:
                processed_parts.append(part)
            else:
                # Handle parentheses specially - they should be preserved for grouping
                if "(" in part or ")" in part:
                    # Parse parenthetical expressions carefully
                    processed_part = self._prepare_parenthetical_term(part)
                    processed_parts.append(processed_part)
                else:
                    # This is a search term - for Boolean queries, don't add prefix wildcards
                    prepared_term = self._prepare_single_term(part, is_prefix=False)
                    processed_parts.append(prepared_term)

        return " ".join(processed_parts)

    def _prepare_parenthetical_term(self, term: str) -> str:
        """Prepare a term that contains parentheses, preserving the parentheses for grouping.

        Args:
            term: A term that may contain parentheses like "(hello" or "world)" or "(hello OR world)"

        Returns:
            A properly formatted term with parentheses preserved
        """
        # Handle terms that start/end with parentheses but may contain quotable content
        result = ""
        i = 0
        while i < len(term):
            if term[i] in "()":
                # Preserve parentheses as-is
                result += term[i]
                i += 1
            else:
                # Find the next parenthesis or end of string
                start = i
                while i < len(term) and term[i] not in "()":
                    i += 1

                # Extract the content between parentheses
                content = term[start:i].strip()
                if content:
                    # Only quote if it actually needs quoting (has hyphens, special chars, etc)
                    # but don't quote if it's just simple words
                    if self._needs_quoting(content):
                        escaped_content = content.replace('"', '""')
                        result += f'"{escaped_content}"'
                    else:
                        result += content

        return result

    def _needs_quoting(self, term: str) -> bool:
        """Check if a term needs to be quoted for FTS5 safety.

        Args:
            term: The term to check

        Returns:
            True if the term should be quoted
        """
        if not term or not term.strip():
            return False

        # Characters that indicate we should quote (excluding parentheses which are valid syntax)
        needs_quoting_chars = [
            " ",
            ".",
            ":",
            ";",
            ",",
            "<",
            ">",
            "?",
            "/",
            "-",
            "'",
            '"',
            "[",
            "]",
            "{",
            "}",
            "+",
            "!",
            "@",
            "#",
            "$",
            "%",
            "^",
            "&",
            "=",
            "|",
            "\\",
            "~",
            "`",
        ]

        return any(c in term for c in needs_quoting_chars)

    def _prepare_single_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a single search term (no Boolean operators).

        Args:
            term: A single search term
            is_prefix: Whether to add prefix search capability (* suffix)

        Returns:
            A properly formatted single term
        """
        if not term or not term.strip():
            return term

        term = term.strip()

        # Check if term is already a proper wildcard pattern (alphanumeric + *)
        # e.g., "hello*", "test*world" - these should be left alone
        if "*" in term and all(c.isalnum() or c in "*_-" for c in term):
            return term

        # Characters that can cause FTS5 syntax errors when used as operators
        # We're more conservative here - only quote when we detect problematic patterns
        problematic_chars = [
            '"',
            "'",
            "(",
            ")",
            "[",
            "]",
            "{",
            "}",
            "+",
            "!",
            "@",
            "#",
            "$",
            "%",
            "^",
            "&",
            "=",
            "|",
            "\\",
            "~",
            "`",
        ]

        # Characters that indicate we should quote (spaces, dots, colons, etc.)
        # Adding hyphens here because FTS5 can have issues with hyphens followed by wildcards
        needs_quoting_chars = [" ", ".", ":", ";", ",", "<", ">", "?", "/", "-"]

        # Check if term needs quoting
        has_problematic = any(c in term for c in problematic_chars)
        has_spaces_or_special = any(c in term for c in needs_quoting_chars)

        if has_problematic or has_spaces_or_special:
            # Handle multi-word queries differently from special character queries
            if " " in term and not any(c in term for c in problematic_chars):
                # Check if any individual word contains special characters that need quoting
                words = term.strip().split()
                has_special_in_words = any(
                    any(c in word for c in needs_quoting_chars if c != " ") for word in words
                )

                if not has_special_in_words:
                    # For multi-word queries with simple words (like "emoji unicode"),
                    # use boolean AND to handle word order variations
                    if is_prefix:
                        # Add prefix wildcard to each word for better matching
                        prepared_words = [f"{word}*" for word in words if word]
                    else:
                        prepared_words = words
                    term = " AND ".join(prepared_words)
                else:
                    # If any word has special characters, quote the entire phrase
                    escaped_term = term.replace('"', '""')
                    if is_prefix and not ("/" in term and term.endswith(".md")):
                        term = f'"{escaped_term}"*'
                    else:
                        term = f'"{escaped_term}"'
            else:
                # For terms with problematic characters or file paths, use exact phrase matching
                # Escape any existing quotes by doubling them
                escaped_term = term.replace('"', '""')
                # Quote the entire term to handle special characters safely
                if is_prefix and not ("/" in term and term.endswith(".md")):
                    # For search terms (not file paths), add prefix matching
                    term = f'"{escaped_term}"*'
                else:
                    # For file paths, use exact matching
                    term = f'"{escaped_term}"'
        elif is_prefix:
            # Only add wildcard for simple terms without special characters
            term = f"{term}*"

        return term

    def _prepare_search_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a search term for FTS5 query.

        Args:
            term: The search term to prepare
            is_prefix: Whether to add prefix search capability (* suffix)

        For FTS5:
        - Boolean operators (AND, OR, NOT) are preserved for complex queries
        - Terms with FTS5 special characters are quoted to prevent syntax errors
        - Simple terms get prefix wildcards for better matching
        """
        # Check for explicit boolean operators - if present, process as Boolean query
        boolean_operators = [" AND ", " OR ", " NOT "]
        if any(op in f" {term} " for op in boolean_operators):
            return self._prepare_boolean_query(term)

        # For non-Boolean queries, use the single term preparation logic
        return self._prepare_single_term(term, is_prefix)

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
        """Search across all indexed content using SQLite FTS5."""
        conditions = []
        params = {}
        order_by_clause = ""

        # Handle text search for title and content
        if search_text:
            # Skip FTS for wildcard-only queries that would cause "unknown special query" errors
            if search_text.strip() == "*" or search_text.strip() == "":
                # For wildcard searches, don't add any text conditions - return all results
                pass
            else:
                # Use _prepare_search_term to handle both Boolean and non-Boolean queries
                processed_text = self._prepare_search_term(search_text.strip())
                params["text"] = processed_text
                conditions.append("(title MATCH :text OR content_stems MATCH :text)")

        # Handle title match search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            conditions.append("title MATCH :title_text")

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("permalink = :permalink")

        # Handle permalink match search, supports *
        if permalink_match:
            # For GLOB patterns, don't use _prepare_search_term as it will quote slashes
            # GLOB patterns need to preserve their syntax
            permalink_text = permalink_match.lower().strip()
            params["permalink"] = permalink_text
            if "*" in permalink_match:
                conditions.append("permalink GLOB :permalink")
            else:
                # For exact matches without *, we can use FTS5 MATCH
                # but only prepare the term if it doesn't look like a path
                if "/" in permalink_text:
                    conditions.append("permalink = :permalink")
                else:
                    permalink_text = self._prepare_search_term(permalink_text, is_prefix=False)
                    params["permalink"] = permalink_text
                    conditions.append("permalink MATCH :permalink")

        # Handle entity type filter
        if search_item_types:
            type_list = ", ".join(f"'{t.value}'" for t in search_item_types)
            conditions.append(f"type IN ({type_list})")

        # Handle type filter
        if types:
            type_list = ", ".join(f"'{t}'" for t in types)
            conditions.append(f"json_extract(metadata, '$.entity_type') IN ({type_list})")

        # Handle date filter using datetime() for proper comparison
        if after_date:
            params["after_date"] = after_date
            conditions.append("datetime(created_at) > datetime(:after_date)")

            # order by most recent first
            order_by_clause = ", updated_at DESC"

        # Always filter by project_id
        params["project_id"] = self.project_id
        conditions.append("project_id = :project_id")

        # set limit on search query
        params["limit"] = limit
        params["offset"] = offset

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

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
                bm25(search_index) as score
            FROM search_index
            WHERE {where_clause}
            ORDER BY score ASC {order_by_clause}
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")
        try:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
        except Exception as e:
            # Handle FTS5 syntax errors and provide user-friendly feedback
            if "fts5: syntax error" in str(e).lower():  # pragma: no cover
                logger.warning(f"FTS5 syntax error for search term: {search_text}, error: {e}")
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
                score=row.score,
                metadata=json.loads(row.metadata) if row.metadata else {},
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
