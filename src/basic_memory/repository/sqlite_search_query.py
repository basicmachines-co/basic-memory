"""SQLite FTS5 query preparation: term syntax and filter compilation.

Pure functions over a ``ProjectScope`` and the caller's filters. Nothing here opens
a session or owns an index; the repository resolves the one piece of live schema
state the compiler needs (the entity table's columns) and passes it in.
"""

import re
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from basic_memory.repository.metadata_filters import build_sqlite_json_path, parse_metadata_filters
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.search_filters import (
    AFTER_DATE_ORDER_BY,
    SQLITE_FILTER_DIALECT,
    CompiledFilter,
    shared_filter_conditions,
)
from basic_memory.repository.search_query import relaxed_query_words
from basic_memory.repository.search_repository_base import (
    SearchIndexKey,
    metadata_contains_like_condition,
    metadata_filter_content_type_condition,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchItemType
from basic_memory.temporal import TemporalFilter

SQLITE_WORD_COLUMNS = "{title content_stems content_snippet}"

# Characters that indicate a term should be quoted (parentheses excluded: valid syntax).
_NEEDS_QUOTING_CHARS = frozenset(" .:;,<>?/-'\"[]{}+!@#$%^&=|\\~`")
# Characters that can cause FTS5 syntax errors when read as operators.
_PROBLEMATIC_CHARS = frozenset("\"'()[]{}+!@#$%^&=|\\~`")
# Characters that indicate quoting for spaces, dots, colons, and hyphens followed by
# wildcards, which FTS5 mishandles.
_SPACE_OR_SPECIAL_CHARS = frozenset(" .:;,<>?/-")
_BOOLEAN_OPERATOR_PATTERN = r"(\bAND\b|\bOR\b|\bNOT\b)"


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
    *,
    entity_columns: Collection[str],
    search_text: str | None = None,
    permalink: str | None = None,
    permalink_match: str | None = None,
    title: str | None = None,
    note_types: Sequence[str] | None = None,
    after_date: datetime | None = None,
    search_item_types: Sequence[SearchItemType] | None = None,
    categories: Sequence[str] | None = None,
    metadata_filters: dict[str, Any] | None = None,
    file_path_prefix: str | None = None,
    temporal: TemporalFilter | None = None,
    candidate_keys: Sequence[SearchIndexKey] | None = None,
) -> CompiledFilter:
    """Compile SQLite FTS FROM/WHERE/score shared by search and count.

    ``entity_columns`` is the live column set of the ``entity`` table. Generated
    frontmatter columns are used when present and fall back to ``json_extract``.
    """
    params: dict[str, Any] = {}
    conditions = shared_filter_conditions(
        scope,
        params,
        dialect=SQLITE_FILTER_DIALECT,
        permalink=permalink,
        file_path_prefix=file_path_prefix,
        candidate_keys=candidate_keys,
        search_item_types=search_item_types,
        categories=categories,
        note_types=note_types,
        after_date=after_date,
        temporal=temporal,
    )
    match_conditions: list[str] = []
    from_clause = "search_index"
    score_expression = "bm25(search_index)"
    preserve_match_score = False

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

    if title:
        params["title_text"] = prepare_search_term(title.strip(), is_prefix=False)
        match_conditions.append("search_index.title MATCH :title_text")

    if permalink_match:
        # GLOB patterns keep their syntax; prepare_search_term would quote the slashes.
        permalink_text = permalink_match.lower().strip()
        params["permalink"] = permalink_text
        if "*" in permalink_match:
            conditions.append("search_index.permalink GLOB :permalink")
        elif "/" in permalink_text:
            conditions.append("search_index.permalink = :permalink")
        else:
            # A bare name without a path matches through FTS5.
            params["permalink"] = prepare_search_term(permalink_text, is_prefix=False)
            match_conditions.append("search_index.permalink MATCH :permalink")

    if metadata_filters:
        parsed_filters = parse_metadata_filters(metadata_filters)
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

    return CompiledFilter(
        from_clause=from_clause,
        where_clause=" AND ".join(conditions),
        params=params,
        order_by_clause=AFTER_DATE_ORDER_BY if after_date else "",
        score_expression=score_expression,
    )
