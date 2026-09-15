"""Read-only FTS query compilation for explicit project scopes."""

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.search_query import relaxed_query_words
from basic_memory.repository.search_repository_base import (
    SearchIndexKey,
    candidate_key_restriction_condition,
    file_path_prefix_condition,
    metadata_contains_like_condition,
    metadata_filter_content_type_condition,
)
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.metadata_filters import parse_metadata_filters, build_sqlite_json_path
from basic_memory.repository.note_type_filters import (
    SQLITE_NOTE_TYPE_VALUE,
    build_note_type_predicate,
)
from basic_memory.repository.temporal_filters import build_temporal_predicate
from basic_memory.schemas.search import SearchItemType
from basic_memory.temporal import TemporalFilter


SQLITE_WORD_COLUMNS = "{title content_stems content_snippet}"


class SQLiteSearchQuery:
    """Compile FTS and filter predicates without owning index mutations."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        project_ids: Sequence[int],
    ) -> None:
        self.session_maker = session_maker
        self._entity_columns: set[str] | None = None
        # A missing scope is never unrestricted. Empty scopes compile to no rows.
        ids = tuple(project_ids)
        if any(type(project_id) is not int or project_id <= 0 for project_id in ids):
            raise ValueError("Project IDs must be positive integers")
        ids = tuple(sorted(set(ids)))
        self._scope_params = {f"scope_{index}": value for index, value in enumerate(ids)}
        self._scope_sql = (
            "IN (" + ", ".join(f":{key}" for key in self._scope_params) + ")"
            if ids
            else "IN (NULL)"
        )

    async def _get_entity_columns(self) -> set[str]:
        if self._entity_columns is None:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text("PRAGMA table_info(entity)"))
                self._entity_columns = {row[1] for row in result.fetchall()}
        return self._entity_columns

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

        # Natural-language queries arrive with sentence punctuation that FTS5
        # treats as syntax ("When did Melanie paint a sunrise?"). The tokenizer
        # ignores this punctuation in the INDEX, so stripping it from word
        # edges loses nothing — but leaving it forces the whole question into
        # an exact-phrase match that returns zero rows, silently disabling the
        # FTS half of hybrid search. Interior characters (hyphens, slashes —
        # permalinks and paths) are untouched.
        if " " in term:
            words = [word.strip("?!.,;:") for word in term.split()]
            term = " ".join(word for word in words if word)
            if not term:
                return ""

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
                        term = f'"{escaped_term}"'  # pragma: no cover
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

    @staticmethod
    def _relaxed_fts_term(word: str) -> str:
        """Render one relaxed word as an FTS5-safe prefix expression.

        A word token can contain an apostrophe ("об'єкт", "don't"). Interpolated
        bare it is FTS5 syntax, not text: the whole expression fails to parse, the
        caller swallows the syntax error, and the relaxed retry returns nothing —
        the exact silent-empty-FTS failure this fallback exists to prevent.
        """
        if "'" in word or '"' in word:
            return '"{}"*'.format(word.replace('"', '""'))
        return f"{word}*"

    @staticmethod
    def _relaxed_fts_text(search_text: Optional[str]) -> Optional[str]:
        """OR-relaxed FTS5 expression for a failed strict query, or None."""
        words = relaxed_query_words(search_text)
        if not words:
            return None
        return " OR ".join(SQLiteSearchQuery._relaxed_fts_term(word) for word in words)

    async def _build_fts_query_parts(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        categories: Optional[List[str]] = None,
        metadata_filters: Optional[dict[str, Any]] = None,
        file_path_prefix: Optional[str] = None,
        temporal: Optional[TemporalFilter] = None,
        candidate_keys: Sequence[SearchIndexKey] | None = None,
    ) -> tuple[str, str, dict[str, Any], str, str]:
        """Build SQLite FTS FROM/WHERE params shared by search and count."""
        conditions = []
        match_conditions = []
        params: dict[str, Any] = dict(self._scope_params)
        order_by_clause = ""
        from_clause = "search_index"
        score_expression = "bm25(search_index)"
        preserve_match_score = False

        # Handle text search for title and content
        if search_text:
            # Skip FTS for wildcard-only queries that would cause "unknown special query" errors
            if search_text.strip() == "*" or search_text.strip() == "":
                # For wildcard searches, don't add any text conditions - return all results
                pass
            else:
                script_query = analyze_script_query(search_text.strip())
                # Trigger: the query contains text from an unsegmented script.
                # Why: the script channel needs one table-level MATCH alongside word fields.
                # Outcome: mixed queries rank all terms together; word-only queries retain their
                # established per-column matching and ranking behavior.
                if script_query.gram_phrases:
                    preserve_match_score = True
                    params["text"] = ""
                    params["script_text"] = ""
                    if script_query.word_text:
                        prepared_text = self._prepare_search_term(script_query.word_text)
                        params["text"] = (
                            f"(title: ({prepared_text}) OR "
                            f"content_stems: ({prepared_text}) OR "
                            f"content_snippet: ({prepared_text}))"
                        )
                    script_phrases = " AND ".join(
                        f'"{" ".join(phrase)}"' for phrase in script_query.gram_phrases
                    )
                    script_clause = f"script_ngrams: ({script_phrases})"
                    params["script_text"] = (
                        f" AND ({script_clause})" if script_query.word_text else script_clause
                    )
                    match_conditions.append("search_index MATCH (:text || :script_text)")
                else:
                    word_text = (
                        script_query.word_text
                        if script_query.word_text is not None
                        else search_text.strip()
                    )
                    processed_text = self._prepare_search_term(word_text)
                    params["text"] = processed_text
                    # content_stems is capped for Postgres index-row compatibility, while
                    # SQLite stores the complete note body in its FTS5 content_snippet column.
                    match_conditions.append(
                        "(search_index.title MATCH :text OR "
                        "search_index.content_stems MATCH :text OR "
                        "search_index.content_snippet MATCH :text)"
                    )

        # Handle title match search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            match_conditions.append("search_index.title MATCH :title_text")

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("search_index.permalink = :permalink")

        # Handle permalink match search, supports *
        if permalink_match:
            # For GLOB patterns, don't use _prepare_search_term as it will quote slashes
            # GLOB patterns need to preserve their syntax
            permalink_text = permalink_match.lower().strip()
            params["permalink"] = permalink_text
            if "*" in permalink_match:
                conditions.append("search_index.permalink GLOB :permalink")
            else:
                # For exact matches without *, we can use FTS5 MATCH
                # but only prepare the term if it doesn't look like a path
                if "/" in permalink_text:
                    conditions.append("search_index.permalink = :permalink")
                else:
                    permalink_text = self._prepare_search_term(permalink_text, is_prefix=False)
                    params["permalink"] = permalink_text
                    match_conditions.append("search_index.permalink MATCH :permalink")

        # Handle directory subtree scope. The predicate is built by the shared
        # helper so SQLite and Postgres scope by the identical rule; see
        # file_path_prefix_condition for the boundary and escaping reasoning.
        subtree_condition = file_path_prefix_condition(file_path_prefix, params)
        if subtree_condition is not None:
            conditions.append(subtree_condition)

        # Handle an explicit candidate-row restriction. Built by the shared helper so
        # both backends restrict by the identical rule; see
        # candidate_key_restriction_condition for why the vector filter pass asks about
        # its candidates rather than paging the filter's whole match set (#1431).
        if candidate_keys is not None:
            conditions.append(candidate_key_restriction_condition(candidate_keys, params))

        # Handle entity type filter (parameterized for defense-in-depth)
        if search_item_types:
            type_placeholders = []
            for idx, t in enumerate(search_item_types):
                param_name = f"search_type_{idx}"
                params[param_name] = t.value
                type_placeholders.append(f":{param_name}")
            conditions.append(f"search_index.type IN ({', '.join(type_placeholders)})")

        # Handle observation category filter (parameterized for defense-in-depth).
        # Trigger: caller passed `categories` to scope observation results.
        # Why: `entity_types=["observation"]` only narrows to the observation row type;
        #      callers expect exact-category matching, not incidental text matches.
        # Outcome: only rows whose indexed category exactly equals a requested value
        #          survive (entities/relations have NULL category and are excluded).
        if categories:
            category_placeholders = []
            for idx, category in enumerate(categories):
                param_name = f"category_{idx}"
                params[param_name] = category
                category_placeholders.append(f":{param_name}")
            conditions.append(f"search_index.category IN ({', '.join(category_placeholders)})")

        # Handle note type filter (frontmatter type field, parameterized).
        # Trigger: caller passed `note_types` to scope by the frontmatter `type` field.
        # Why: the type belongs to the note, but only its entity row carries the
        #      frontmatter; observation and relation rows do not. Reading it off each row
        #      silently excluded every non-entity row, which made `note_types` combined
        #      with a valid-time filter unsatisfiable.
        # Outcome: resolved through the owning note in one shared builder, so both
        #          backends ask the same question and observation rows of a matching note
        #          are admitted.
        if note_types:
            conditions.append(
                build_note_type_predicate(
                    note_types,
                    params,
                    project_scope_sql=self._scope_sql,
                    note_type_value=SQLITE_NOTE_TYPE_VALUE,
                )
            )

        # Handle date filter using datetime() for proper comparison
        if after_date:
            params["after_date"] = after_date
            # Filter on updated_at so recently-edited notes are included even when created_at is old
            conditions.append("datetime(search_index.updated_at) > datetime(:after_date)")

            # order by most recent first
            order_by_clause = ", search_index.updated_at DESC"

        # Handle authored valid time (SPEC-82).
        # Trigger: caller asked when a statement was true of the world.
        # Why: `after_date` above filters `updated_at`, which records when the note was
        #      last edited. That is bookkeeping, never a semantic claim; a decision
        #      effective through July says nothing about when its file was touched.
        # Outcome: an independent predicate over the temporal projection. It matches
        #          only sources carrying a structured qualifier, so undated sources are
        #          excluded whenever a valid-time filter is present, and no ordering
        #          changes -- relevance still decides the ranking.
        if temporal is not None:
            conditions.append(
                build_temporal_predicate(temporal, params, project_scope_sql=self._scope_sql)
            )

        # Handle structured metadata filters (frontmatter)
        if metadata_filters:
            parsed_filters = parse_metadata_filters(metadata_filters)
            from_clause = "search_index JOIN entity ON search_index.entity_id = entity.id"
            # Frontmatter filters answer for notes only; see
            # metadata_filter_content_type_condition for why every regular file
            # would otherwise satisfy a null predicate.
            conditions.append(metadata_filter_content_type_condition(params))
            entity_columns = await self._get_entity_columns()

            for idx, filt in enumerate(parsed_filters):
                path_param = f"meta_path_{idx}"
                extract_expr = None
                use_tags_column = False

                if filt.path_parts == ["status"] and "frontmatter_status" in entity_columns:
                    extract_expr = "entity.frontmatter_status"
                elif filt.path_parts == ["type"] and "frontmatter_type" in entity_columns:
                    extract_expr = "entity.frontmatter_type"
                elif filt.path_parts == ["tags"] and "tags_json" in entity_columns:
                    extract_expr = "entity.tags_json"
                    use_tags_column = True

                if extract_expr is None:
                    params[path_param] = build_sqlite_json_path(filt.path_parts)
                    extract_expr = f"json_extract(entity.entity_metadata, :{path_param})"

                # json_extract returns SQL NULL both for a missing key and for an
                # explicit JSON null, and the generated frontmatter_* columns are
                # that same json_extract — so IS NULL means "the note carries no
                # value here", the question `{"owner": None}` asks. `= NULL` is
                # never true, so equality here would report a confident zero.
                if filt.op == "is_null":
                    conditions.append(f"{extract_expr} IS NULL")
                    continue

                if filt.op == "eq":
                    value_param = f"meta_val_{idx}"
                    params[value_param] = filt.value
                    conditions.append(f"{extract_expr} = :{value_param}")
                    continue

                if filt.op == "in":
                    placeholders = []
                    for j, val in enumerate(filt.value):
                        value_param = f"meta_val_{idx}_{j}"
                        params[value_param] = val
                        placeholders.append(f":{value_param}")
                    conditions.append(f"{extract_expr} IN ({', '.join(placeholders)})")
                    continue

                if filt.op == "contains":
                    tag_conditions = []
                    for j, val in enumerate(filt.value):
                        value_param = f"meta_val_{idx}_{j}"
                        params[value_param] = val
                        # The exact JSON-membership test is the primary path; the
                        # substring patterns only reach values stored as array text.
                        like_condition = metadata_contains_like_condition(
                            extract_expr,
                            val,
                            param_prefix=value_param,
                            params=params,
                        )
                        json_each_expr = (
                            "json_each(entity.tags_json)"
                            if use_tags_column
                            else f"json_each(entity.entity_metadata, :{path_param})"
                        )
                        tag_conditions.append(
                            "("
                            f"EXISTS (SELECT 1 FROM {json_each_expr} WHERE value = :{value_param}) "
                            f"OR {like_condition}"
                            ")"
                        )
                    conditions.append(" AND ".join(tag_conditions))
                    continue

                if filt.op in {"gt", "gte", "lt", "lte", "between"}:
                    compare_expr = (
                        f"CAST({extract_expr} AS REAL)"
                        if filt.comparison == "numeric"
                        else extract_expr
                    )

                    if filt.op == "between":
                        min_param = f"meta_val_{idx}_min"
                        max_param = f"meta_val_{idx}_max"
                        params[min_param] = filt.value[0]
                        params[max_param] = filt.value[1]
                        conditions.append(f"{compare_expr} BETWEEN :{min_param} AND :{max_param}")
                    else:
                        value_param = f"meta_val_{idx}"
                        params[value_param] = filt.value
                        operator = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[filt.op]
                        conditions.append(f"{compare_expr} {operator} :{value_param}")
                    continue

        # Trigger: SQLite rejects some Boolean combinations of MATCH predicates,
        # including a word-field OR expression combined with the script channel.
        # Why: each MATCH must be evaluated in an FTS-valid query context.
        # Outcome: keep one outer MATCH for bm25 ranking and intersect the rest by rowid.
        if len(match_conditions) > 1:
            ranked_match, *additional_matches = match_conditions
            conditions.extend(
                f"search_index.rowid IN (SELECT rowid FROM search_index WHERE {match_condition})"
                for match_condition in additional_matches
            )
            match_conditions = [ranked_match]

        # Trigger: SQLite FTS MATCH predicates combined with JOINs can fail with
        # "unable to use function MATCH in the requested context".
        # Why: script queries need MATCH and bm25 together for ranking, while legacy
        # word-column OR predicates cannot evaluate bm25 in the same derived query.
        # Outcome: rank script matches before joining metadata; retain the established
        # rowid-filter path for word-only searches.
        if metadata_filters and match_conditions:
            match_where = " AND ".join(match_conditions)
            if preserve_match_score:
                from_clause = (
                    "(SELECT search_index.rowid AS rowid, search_index.*, "
                    "bm25(search_index) AS fts_score "
                    f"FROM search_index WHERE {match_where}) AS search_index "
                    "JOIN entity ON search_index.entity_id = entity.id"
                )
                score_expression = "search_index.fts_score"
            else:
                conditions.append(
                    f"search_index.rowid IN (SELECT rowid FROM search_index WHERE {match_where})"
                )
        else:
            conditions.extend(match_conditions)

        conditions.append(f"search_index.project_id {self._scope_sql}")

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        return from_clause, where_clause, params, order_by_clause, score_expression
