"""SQLite FTS5 query preparation and execution.

Term preparation and filter compilation are pure functions over a ``ProjectScope`` and
a ``PreparedSearchQuery``. ``SQLiteFts`` runs the compiled statement and owns FTS5's
failure semantics. Nothing here initializes or mutates an index.
"""

import re
import time
from collections.abc import Collection, Sequence
from typing import Any

import logfire
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.metadata_filters import build_sqlite_json_path, parse_metadata_filters
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.search_filters import (
    AFTER_DATE_ORDER_BY,
    SQLITE_FILTER_DIALECT,
    CompiledFilter,
    metadata_contains_like_condition,
    metadata_filter_content_type_condition,
    shared_filter_conditions,
)
from basic_memory.repository.search_index_row import SearchIndexKey, SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery, relaxed_query_words
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import SearchTraceCollector, build_fts_page_stage

SQLITE_WORD_COLUMNS = "{title content_stems content_snippet}"

# Characters that indicate a term should be quoted (parentheses excluded: valid syntax).
_NEEDS_QUOTING_CHARS = frozenset(" .:;,<>?/-'\"[]{}+!@#$%^&=|\\~`")
# Characters that can cause FTS5 syntax errors when read as operators.
_PROBLEMATIC_CHARS = frozenset("\"'()[]{}+!@#$%^&=|\\~`")
# Characters that indicate quoting for spaces, dots, colons, and hyphens followed by
# wildcards, which FTS5 mishandles.
_SPACE_OR_SPECIAL_CHARS = frozenset(" .:;,<>?/-")
_BOOLEAN_OPERATOR_PATTERN = r"(\bAND\b|\bOR\b|\bNOT\b)"

# Every FTS statement returns these columns plus a score.
_RESULT_COLUMNS = """
                search_index.project_id,
                search_index.id,
                search_index.title,
                search_index.permalink,
                search_index.file_path,
                search_index.type,
                search_index.metadata,
                search_index.from_id,
                search_index.to_id,
                search_index.relation_type,
                search_index.entity_id,
                search_index.content_snippet,
                search_index.category,
                search_index.created_at,
                search_index.updated_at"""


# --- Term preparation ---


def needs_quoting(term: str) -> bool:
    """Whether a term must be quoted for FTS5 safety."""
    if not term or not term.strip():
        return False
    return any(c in _NEEDS_QUOTING_CHARS for c in term)


def prepare_single_term(term: str, is_prefix: bool = True) -> str:
    """Prepare one search term with no Boolean operators.

    ``is_prefix`` adds the ``*`` suffix so simple terms match by prefix.
    """
    if not term or not term.strip():
        return term

    term = term.strip()

    # A proper wildcard pattern ("hello*", "test*world") is left alone.
    if "*" in term and all(c.isalnum() or c in "*_-" for c in term):
        return term

    # Natural-language queries arrive with sentence punctuation that FTS5 treats as
    # syntax ("When did Melanie paint a sunrise?"). The tokenizer ignores this
    # punctuation in the index, so stripping it from word edges loses nothing, but
    # leaving it forces the whole question into an exact-phrase match that returns
    # zero rows and silently disables the FTS half of hybrid search. Interior
    # characters (hyphens, slashes in permalinks and paths) are untouched.
    if " " in term:
        words = [word.strip("?!.,;:") for word in term.split()]
        term = " ".join(word for word in words if word)
        if not term:
            return ""

    has_problematic = any(c in _PROBLEMATIC_CHARS for c in term)
    has_spaces_or_special = any(c in _SPACE_OR_SPECIAL_CHARS for c in term)

    if has_problematic or has_spaces_or_special:
        if " " in term and not has_problematic:
            words = term.split()
            has_special_in_words = any(
                any(c in word for c in _SPACE_OR_SPECIAL_CHARS if c != " ") for word in words
            )
            if not has_special_in_words:
                # Multi-word queries of simple words ("emoji unicode") use Boolean AND
                # so word order does not matter.
                prepared_words = [f"{word}*" for word in words] if is_prefix else words
                return " AND ".join(prepared_words)
            # Any word with special characters quotes the entire phrase.
            escaped_term = term.replace('"', '""')
            if is_prefix and not ("/" in term and term.endswith(".md")):
                return f'"{escaped_term}"*'
            return f'"{escaped_term}"'  # pragma: no cover

        # Terms with problematic characters or file paths use exact phrase matching.
        escaped_term = term.replace('"', '""')
        if is_prefix and not ("/" in term and term.endswith(".md")):
            return f'"{escaped_term}"*'
        return f'"{escaped_term}"'

    if is_prefix:
        return f"{term}*"
    return term


def prepare_parenthetical_term(term: str) -> str:
    """Prepare a term containing parentheses, preserving them for grouping."""
    result = ""
    index = 0
    while index < len(term):
        if term[index] in "()":
            result += term[index]
            index += 1
            continue
        start = index
        while index < len(term) and term[index] not in "()":
            index += 1
        content = term[start:index].strip()
        if content:
            # Quote only when the content needs it; simple words stay bare.
            if needs_quoting(content):
                escaped_content = content.replace('"', '""')
                result += f'"{escaped_content}"'
            else:
                result += content
    return result


def prepare_boolean_query(query: str) -> str:
    """Quote the terms of a Boolean query while preserving its operators and grouping."""
    processed_parts: list[str] = []
    for part in re.split(_BOOLEAN_OPERATOR_PATTERN, query):
        part = part.strip()
        if not part:
            continue
        if part in ("AND", "OR", "NOT"):
            processed_parts.append(part)
        elif "(" in part or ")" in part:
            processed_parts.append(prepare_parenthetical_term(part))
        else:
            # Boolean queries do not get prefix wildcards.
            processed_parts.append(prepare_single_term(part, is_prefix=False))
    return " ".join(processed_parts)


def prepare_search_term(term: str, is_prefix: bool = True) -> str:
    """Prepare user text as an FTS5 query.

    Boolean operators (AND, OR, NOT) are preserved. Terms with FTS5 special
    characters are quoted. Simple terms get prefix wildcards.
    """
    if any(op in f" {term} " for op in (" AND ", " OR ", " NOT ")):
        return prepare_boolean_query(term)
    return prepare_single_term(term, is_prefix)


def _relaxed_fts_term(word: str) -> str:
    """Render one relaxed word as an FTS5-safe prefix expression.

    A word token can contain an apostrophe ("об'єкт", "don't"). Interpolated bare it
    is FTS5 syntax, not text: the whole expression fails to parse, the caller swallows
    the syntax error, and the relaxed retry returns nothing, which is the silent-empty
    FTS failure this fallback exists to prevent.
    """
    if "'" in word or '"' in word:
        return '"{}"*'.format(word.replace('"', '""'))
    return f"{word}*"


def relaxed_fts_text(search_text: str | None) -> str | None:
    """OR-relaxed FTS5 expression for a failed strict query, or None."""
    words = relaxed_query_words(search_text)
    if not words:
        return None
    return " OR ".join(_relaxed_fts_term(word) for word in words)


def is_fts5_syntax_error(exc: Exception) -> bool:
    return "fts5: syntax error" in str(exc).lower()


# --- Filter compilation ---


def compile_fts_filter(
    scope: ProjectScope,
    query: PreparedSearchQuery,
    *,
    entity_columns: Collection[str],
    candidate_keys: Sequence[SearchIndexKey] | None = None,
) -> CompiledFilter:
    """Compile SQLite FTS FROM/WHERE/score shared by search and count.

    ``entity_columns`` is the live column set of the ``entity`` table. Generated
    frontmatter columns are used when present and fall back to ``json_extract``.
    """
    params: dict[str, Any] = {}
    conditions = shared_filter_conditions(
        scope, params, dialect=SQLITE_FILTER_DIALECT, query=query, candidate_keys=candidate_keys
    )
    match_conditions: list[str] = []
    from_clause = "search_index"
    score_expression = "bm25(search_index)"
    preserve_match_score = False
    search_text = query.search_text

    # Wildcard-only and blank text add no text condition: every row matches.
    if search_text and search_text.strip() not in ("", "*"):
        script_query = analyze_script_query(search_text.strip())
        # Trigger: the query contains text from an unsegmented script.
        # Why: the script channel needs one table-level MATCH alongside word fields.
        # Outcome: mixed queries rank all terms together; word-only queries retain
        # their established per-column matching and ranking behavior.
        if script_query.gram_phrases:
            preserve_match_score = True
            params["text"] = ""
            params["script_text"] = ""
            if script_query.word_text:
                prepared_text = prepare_search_term(script_query.word_text)
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
            params["text"] = prepare_search_term(word_text)
            # content_stems is capped for Postgres index-row compatibility, while
            # SQLite stores the complete note body in its FTS5 content_snippet column.
            match_conditions.append(
                "(search_index.title MATCH :text OR "
                "search_index.content_stems MATCH :text OR "
                "search_index.content_snippet MATCH :text)"
            )

    if query.title:
        params["title_text"] = prepare_search_term(query.title.strip(), is_prefix=False)
        match_conditions.append("search_index.title MATCH :title_text")

    if query.permalink_match:
        # GLOB patterns keep their syntax; prepare_search_term would quote the slashes.
        permalink_text = query.permalink_match.lower().strip()
        params["permalink"] = permalink_text
        if "*" in query.permalink_match:
            conditions.append("search_index.permalink GLOB :permalink")
        elif "/" in permalink_text:
            conditions.append("search_index.permalink = :permalink")
        else:
            # A bare name without a path matches through FTS5.
            params["permalink"] = prepare_search_term(permalink_text, is_prefix=False)
            match_conditions.append("search_index.permalink MATCH :permalink")

    if query.metadata_filters:
        parsed_filters = parse_metadata_filters(query.metadata_filters)
        from_clause = "search_index JOIN entity ON search_index.entity_id = entity.id"
        # Frontmatter filters answer for notes only; see
        # metadata_filter_content_type_condition for why every regular file would
        # otherwise satisfy a null predicate.
        conditions.append(metadata_filter_content_type_condition(params))

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

            # json_extract returns SQL NULL both for a missing key and for an explicit
            # JSON null, and the generated frontmatter_* columns are that same
            # json_extract, so IS NULL means "the note carries no value here", the
            # question ``{"owner": None}`` asks. ``= NULL`` is never true.
            if filt.op == "is_null":
                conditions.append(f"{extract_expr} IS NULL")
                continue

            if filt.op == "eq":
                value_param = f"meta_val_{idx}"
                params[value_param] = filt.value
                conditions.append(f"{extract_expr} = :{value_param}")
                continue

            if filt.op == "in":
                placeholders: list[str] = []
                for j, val in enumerate(filt.value):
                    value_param = f"meta_val_{idx}_{j}"
                    params[value_param] = val
                    placeholders.append(f":{value_param}")
                conditions.append(f"{extract_expr} IN ({', '.join(placeholders)})")
                continue

            if filt.op == "contains":
                tag_conditions: list[str] = []
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
    if query.metadata_filters and match_conditions:
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

    return CompiledFilter(
        from_clause=from_clause,
        where_clause=" AND ".join(conditions),
        params=params,
        order_by_clause=AFTER_DATE_ORDER_BY if query.after_date else "",
        score_expression=score_expression,
    )


# --- Execution ---


class SQLiteFts:
    """Run FTS5 statements for any scope in one database.

    Holds the one piece of live schema state compilation needs: the entity table's
    columns, read once per instance so generated frontmatter columns are used when the
    database has them.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker
        self._entity_columns: frozenset[str] | None = None

    async def _entity_column_names(self) -> frozenset[str]:
        if self._entity_columns is None:
            async with db.scoped_session(self._session_maker) as session:
                result = await session.execute(text("PRAGMA table_info(entity)"))
                self._entity_columns = frozenset(row[1] for row in result.fetchall())
        return self._entity_columns

    async def search(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        allow_relaxed: bool = False,
        session: AsyncSession | None = None,
        candidate_keys: Sequence[SearchIndexKey] | None = None,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Run one FTS5 page.

        ``allow_relaxed=True`` retries a zero-result strict multi-word query with
        OR-joined content terms. Only the hybrid path opts in: its FTS branch otherwise
        contributes nothing for question-form queries. Service-level FTS searches keep
        their own conservative fallback.
        """
        search_text = query.search_text
        # Generated frontmatter columns are read only when a metadata filter needs them.
        entity_columns = (
            await self._entity_column_names() if query.metadata_filters else frozenset()
        )
        compiled = compile_fts_filter(
            scope, query, entity_columns=entity_columns, candidate_keys=candidate_keys
        )
        params = compiled.params
        params["limit"] = limit
        params["offset"] = offset
        relaxed_search_text = search_text
        if search_text and "script_text" in params:
            relaxed_search_text = analyze_script_query(search_text.strip()).word_text

        sql = f"""
            SELECT{_RESULT_COLUMNS},
                {compiled.score_expression} as score
            FROM {compiled.from_clause}
            WHERE {compiled.where_clause}
            ORDER BY score ASC {compiled.order_by_clause}
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")
        fts_started_at = time.perf_counter() if trace is not None else None

        async def run_search(active_session: AsyncSession):
            result = await active_session.execute(text(sql), params)
            rows = result.fetchall()
            relaxed_fallback_used = False
            # Trigger: multi-word natural-language query matched nothing under the
            # default all-terms-AND semantics.
            # Why: questions ("when did X do Y") rarely have every word in one
            # document; without relaxation the FTS half of hybrid search contributes
            # zero candidates and ranking degrades to vector-only.
            # Outcome: one retry with OR-joined prefix terms; bm25 still ranks
            # multi-term matches first.
            relaxed = relaxed_fts_text(relaxed_search_text) if allow_relaxed and not rows else None
            if relaxed and params.get("text"):
                relaxed_fallback_used = True
                params["text"] = (
                    f"{SQLITE_WORD_COLUMNS}: ({relaxed})" if "script_text" in params else relaxed
                )
                logger.debug(
                    "Strict SQLite FTS returned 0 results; retrying relaxed FTS query "
                    f"strict='{search_text}' relaxed='{relaxed}'"
                )
                with logfire.span(
                    "search.relaxed_fts_retry",
                    backend="sqlite",
                    token_count=len(relaxed_query_words(relaxed_search_text) or ()),
                    limit=limit,
                    offset=offset,
                ):
                    result = await active_session.execute(text(sql), params)
                    rows = result.fetchall()
            return rows, relaxed_fallback_used

        try:
            if session is not None:
                rows, relaxed_fallback_used = await run_search(session)
            else:
                async with db.scoped_session(self._session_maker) as owned_session:
                    rows, relaxed_fallback_used = await run_search(owned_session)
        except Exception as e:
            # An FTS5 syntax error answers with no rows rather than failing the request.
            if is_fts5_syntax_error(e):  # pragma: no cover
                logger.warning(f"FTS5 syntax error for search term: {search_text}, error: {e}")
                if trace is not None:
                    trace.fts = build_fts_page_stage(
                        [],
                        relaxed_fallback_used=False,
                        fts_ms=(
                            (time.perf_counter() - fts_started_at) * 1000
                            if fts_started_at is not None
                            else None
                        ),
                    )
                return []
            logger.error(f"Database error during search: {e}")
            raise

        results = [SearchIndexRow.from_mapping(row._asdict()) for row in rows]
        if trace is not None:
            trace.fts = build_fts_page_stage(
                [((row.type, row.id), row.score or 0.0) for row in results],
                relaxed_fallback_used=relaxed_fallback_used,
                fts_ms=(
                    (time.perf_counter() - fts_started_at) * 1000
                    if fts_started_at is not None
                    else None
                ),
            )

        logger.trace(f"Found {len(results)} search results")
        for r in results:
            logger.trace(
                f"Search result: project_id: {r.project_id} type:{r.type} title: {r.title} permalink: {r.permalink} score: {r.score}"
            )
        return results

    async def count(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        *,
        allow_relaxed: bool = False,
    ) -> int:
        """Count rows matching the FTS5 query, with the same relaxed retry as search."""
        search_text = query.search_text
        entity_columns = (
            await self._entity_column_names() if query.metadata_filters else frozenset()
        )
        compiled = compile_fts_filter(scope, query, entity_columns=entity_columns)
        params = compiled.params
        sql = f"SELECT COUNT(*) FROM {compiled.from_clause} WHERE {compiled.where_clause}"
        logger.trace(f"Count {sql} params: {params}")
        relaxed_search_text = search_text
        if search_text and "script_text" in params:
            relaxed_search_text = analyze_script_query(search_text.strip()).word_text
        try:
            async with db.scoped_session(self._session_maker) as session:
                result = await session.execute(text(sql), params)
                total = int(result.scalar_one())
                relaxed = (
                    relaxed_fts_text(relaxed_search_text) if allow_relaxed and total == 0 else None
                )
                if relaxed and params.get("text"):
                    params["text"] = (
                        f"{SQLITE_WORD_COLUMNS}: ({relaxed})"
                        if "script_text" in params
                        else relaxed
                    )
                    with logfire.span(
                        "search.count.relaxed_fts_retry",
                        backend="sqlite",
                        token_count=len(relaxed_query_words(relaxed_search_text) or ()),
                    ):
                        result = await session.execute(text(sql), params)
                        total = int(result.scalar_one())
                return total
        except Exception as e:
            if is_fts5_syntax_error(e):  # pragma: no cover
                logger.warning(f"FTS5 syntax error for search term: {search_text}, error: {e}")
                return 0
            logger.error(f"Database error during search count: {e}")
            raise
