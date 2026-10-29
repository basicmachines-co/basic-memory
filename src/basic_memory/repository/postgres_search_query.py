"""PostgreSQL tsquery preparation and execution.

Term preparation and filter compilation are pure functions over a ``ProjectScope`` and
a ``PreparedSearchQuery``. ``PostgresFts`` runs the compiled statement and owns
tsquery's failure semantics. Nothing here initializes or mutates an index.
"""

import json
import re
import time
from collections.abc import Sequence
from typing import Any

import logfire
from loguru import logger
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.metadata_filters import parse_metadata_filters
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.search_filters import (
    AFTER_DATE_ORDER_BY,
    POSTGRES_FILTER_DIALECT,
    CompiledFilter,
    metadata_contains_like_condition,
    metadata_filter_content_type_condition,
    shared_filter_conditions,
)
from basic_memory.repository.search_index_row import (
    SearchIndexKey,
    SearchIndexRow,
    capped_snippet_columns,
    snippet_length_columns,
)
from basic_memory.repository.search_query import (
    PreparedSearchQuery,
    relaxation_word_tokens,
    relaxed_query_words,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import SearchTraceCollector, build_fts_page_stage

_TSQUERY_OPERAND_PATTERN = re.compile(r"'(?:''|[^'])*'(?::\*)?|[^\s&|!()]+")
_TSQUERY_WORD_PATTERN = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
_QUOTED_QUERY_PATTERN = re.compile(r'"([^"]*)"')
_MAX_INDEXED_OPERANDS = 128
_BOOLEAN_WORDS = frozenset({"AND", "OR", "NOT"})
_TSQUERY_METACHARACTERS = frozenset("&|!:<>")
# tsquery special characters that must not reach the parser as text.
_TSQUERY_SPECIAL_CHARS = ("&", "|", "!", "(", ")", ":")


def _result_columns(text_columns: str) -> str:
    """The columns every FTS statement returns, plus a score."""
    return f"""
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
                {text_columns},
                search_index.category,
                search_index.created_at,
                search_index.updated_at"""


# --- Term preparation ---


def _tsquery_operands(processed_text: str) -> list[tuple[str, str]]:
    """Return unique (query operand, representative text) pairs in source order."""
    operands: dict[str, str] = {}
    for operand in _TSQUERY_OPERAND_PATTERN.findall(processed_text):
        representative = operand.removesuffix(":*")
        if representative.startswith("'") and representative.endswith("'"):
            representative = representative[1:-1].replace("''", "'")
            operands.setdefault(operand, representative)
            continue

        # An unquoted apostrophe is invalid tsquery syntax. Keep the literal word for
        # the synthetic document, but quote and escape its probe so a strict syntax
        # failure can proceed to the relaxed retry.
        if "'" in representative:
            escaped = "'{}'".format(representative.replace("'", "''"))
            safe_operand = f"{escaped}:*" if operand.endswith(":*") else escaped
            operands.setdefault(safe_operand, representative)
            continue

        # PostgreSQL legitimately parses punctuation inside operands such as
        # ``v0.13.0b2:*`` and ``auth-service:*``. Preserve those bytes so the
        # synthetic document is tokenized the same way as the original note.
        if "<" not in representative and ">" not in representative:
            operands.setdefault(operand, representative)
            continue

        # A malformed strict operand (for example ``foo<bar:*``) must not poison the
        # later relaxed retry. Split punctuation into the same safe word pieces that
        # PostgreSQL will lex, while the original full tsquery still determines
        # whether the strict attempt raises and falls back.
        is_prefix = operand.endswith(":*")
        words = _TSQUERY_WORD_PATTERN.findall(representative)
        for word in words or [representative]:
            escaped_word = "'{}'".format(word.replace("'", "''")) if "'" in word else word
            safe_operand = f"{escaped_word}:*" if is_prefix else escaped_word
            operands.setdefault(safe_operand, word)
    return list(operands.items())


def _render_tsquery_words(
    text_value: str,
    *,
    operator: str,
    is_prefix: bool,
    drop_boolean_words: bool = False,
) -> str:
    """Render user text as complete, individually escaped tsquery operands."""
    words = relaxation_word_tokens(text_value)
    if drop_boolean_words:
        words = [word for word in words if word.upper() not in _BOOLEAN_WORDS]
    if not words:
        return "NOSPECIALCHARS:*"

    operands: list[str] = []
    for word in words:
        escaped_word = "'{}'".format(word.replace("'", "''")) if "'" in word else word
        operands.append(f"{escaped_word}:*" if is_prefix else escaped_word)
    return operator.join(operands)


def _render_boolean_operand(operand: str) -> str:
    """Preserve safe structured text while escaping tsquery syntax bytes."""
    if "'" not in operand and not any(char in _TSQUERY_METACHARACTERS for char in operand):
        return operand
    return _render_tsquery_words(operand, operator=" & ", is_prefix=False)


def _has_valid_boolean_shape(expression: str) -> bool:
    """Reject incomplete operator structure before it reaches strict ``to_tsquery``."""
    depth = 0
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    if depth:
        return False

    stripped = expression.strip()
    if not stripped or stripped[0] in "&|" or stripped[-1] in "&|!":
        return False
    return not any(
        re.search(pattern, stripped)
        for pattern in (
            r"[&|]\s*[&|]",
            r"!\s*[&|)]",
            r"\(\s*[&|)]",
            r"[&|!(]\s*\)",
        )
    )


def prepare_single_term(term: str, is_prefix: bool = True) -> str:
    """Prepare one search term with no Boolean operators.

    Multi-word queries become ``word1 & word2``; ``is_prefix`` adds the ``:*``
    suffix; tsquery special characters are removed.
    """
    if not term or not term.strip():
        return term

    term = term.strip()

    # A wildcard on a single token converts to the tsquery prefix operator. A
    # multi-word query handles each word's wildcard below: converting the whole
    # string would leave words without a "&" between them ("foo cache:*"), which
    # to_tsquery rejects, so the search silently returned nothing.
    if "*" in term and not any(c.isspace() for c in term):
        return term.replace("*", ":*")

    cleaned_term = term
    for char in _TSQUERY_SPECIAL_CHARS:
        cleaned_term = cleaned_term.replace(char, " ")

    if " " in cleaned_term:
        # Strip sentence punctuation from word edges so question-form queries
        # produce clean lexemes (parity with SQLite FTS5 prep). The tsquery tokenizer
        # ignores this punctuation anyway; leaving it only risks syntax errors.
        words = [w.strip("?!.,;") for w in cleaned_term.split()]
        # A trailing "*" asks for a prefix match on that word; a "*" anywhere else is
        # not tsquery syntax and is dropped.
        prepared_words = []
        for word in words:
            wildcard = word.endswith("*")
            word = word.replace("*", "")
            if not word:
                continue
            prepared_words.append(f"{word}:*" if is_prefix or wildcard else word)
        if not prepared_words:
            # Only special characters remained; emit a term that cannot error.
            return "NOSPECIALCHARS:*"
        return " & ".join(prepared_words)

    # Single word: strip edge punctuation and guard the now-empty case so a bare
    # ":*" never reaches tsquery.
    cleaned_term = cleaned_term.strip().strip("?!.,;")
    if not cleaned_term:
        return "NOSPECIALCHARS:*"
    if is_prefix:
        return f"{cleaned_term}:*"
    return cleaned_term


def prepare_boolean_query(query: str) -> str:
    """Convert a Boolean query to tsquery operators (``&``, ``|``, ``!``).

    Examples: ``coffee AND brewing`` -> ``coffee & brewing``;
    ``(pour OR french) AND press`` -> ``(pour | french) & press``;
    ``coffee NOT decaf`` -> ``coffee & !decaf``.
    """
    # PostgreSQL's strict to_tsquery grammar does not accept web-style double quotes.
    # Convert complete quoted groups first so operator-looking words inside them
    # remain text and every word becomes a complete operand.
    quoted_phrases: dict[str, str] = {}

    def replace_quoted_phrase(match: re.Match[str]) -> str:
        phrase = _render_tsquery_words(match.group(1), operator=" & ", is_prefix=False)
        placeholder = f"BMQUOTEDPHRASE{len(quoted_phrases)}"
        while placeholder in query:
            placeholder += "X"
        quoted_phrases[placeholder] = f"({phrase})"
        # Surround the placeholder so quotes adjacent to plain text become explicit
        # operands instead of restoring into ``word(group)``.
        return f" {placeholder} "

    result = _QUOTED_QUERY_PATTERN.sub(replace_quoted_phrase, query)
    if '"' in result:
        # An unmatched quote is user text, not a reason to abort the database
        # transaction. Boolean-looking words lose their operator role here.
        return _render_tsquery_words(query, operator=" & ", is_prefix=True, drop_boolean_words=True)

    # Boolean syntax is the only structure retained from user input. A
    # whitespace-delimited operand still needs explicit conjunctions, but PostgreSQL
    # must tokenize structured single operands such as ``auth-service`` and
    # ``config.json`` exactly as it did before quoted query normalization.
    normalized_parts: list[str] = []
    operator_pattern = r"((?<![^\s()])(?:AND|OR|NOT)(?![^\s()])|[()])"
    for part in re.split(operator_pattern, result):
        stripped_part = part.strip()
        if not stripped_part:
            continue
        if stripped_part in _BOOLEAN_WORDS or stripped_part in {"(", ")"}:
            normalized_parts.append(stripped_part)
            continue
        if stripped_part in quoted_phrases:
            normalized_parts.append(stripped_part)
            continue
        if any(character.isspace() for character in stripped_part):
            normalized_parts.append(
                _render_tsquery_words(stripped_part, operator=" & ", is_prefix=False)
            )
            continue
        normalized_parts.append(_render_boolean_operand(stripped_part))

    # Convert operators from the parsed sequence so ``A NOT B`` does not depend on
    # the final character of A. Structured operands such as C++ may end in
    # punctuation but still require the conjunction before NOT.
    tsquery_parts: list[str] = []
    for part in normalized_parts:
        if part == "AND":
            tsquery_parts.append("&")
            continue
        if part == "OR":
            tsquery_parts.append("|")
            continue
        if part == "NOT":
            if tsquery_parts and tsquery_parts[-1] not in {"&", "|", "!", "("}:
                tsquery_parts.append("&")
            tsquery_parts.append("!")
            continue
        # Quotes are replaced before parentheses are tokenized, so adjacent groups
        # can arrive as separate operands without an explicit AND. PostgreSQL
        # requires that conjunction between an operand or closed group and the next
        # operand or opening group.
        if part != ")" and tsquery_parts and tsquery_parts[-1] not in {"&", "|", "!", "("}:
            tsquery_parts.append("&")
        tsquery_parts.append(part)
    result = " ".join(tsquery_parts)

    # Attach negation to its operand after structural conversion.
    result = re.sub(r"!\s+", "!", result)
    result = re.sub(r"\(\s+", "(", result)
    result = re.sub(r"\s+\)", ")", result)

    if not _has_valid_boolean_shape(result):
        # Keep the searchable words from malformed Boolean input while removing the
        # operators that made its structure incomplete.
        return _render_tsquery_words(query, operator=" & ", is_prefix=True, drop_boolean_words=True)

    # A multi-digit placeholder contains shorter numeric placeholders as prefixes,
    # so restore longest names first to keep each token atomic.
    for placeholder in sorted(quoted_phrases, key=len, reverse=True):
        result = result.replace(placeholder, quoted_phrases[placeholder])

    return result


def prepare_search_term(term: str, is_prefix: bool = True) -> str:
    """Prepare user text as a tsquery.

    Boolean operators convert to tsquery form, prefix matching uses ``:*``, and
    terms are sanitized so they cannot raise tsquery syntax errors.
    """
    if '"' in term or any(op in f" {term} " for op in (" AND ", " OR ", " NOT ")):
        return prepare_boolean_query(term)
    return prepare_single_term(term, is_prefix)


def _relaxed_tsquery_term(word: str) -> str:
    """Render one relaxed word as a tsquery-safe prefix expression.

    Mirrors the SQLite renderer: a word token can contain an apostrophe, and tsquery
    reads that as lexeme-quoting syntax rather than text. Quoting the lexeme and
    doubling any interior quote keeps it literal.
    """
    if "'" in word:
        return "'{}':*".format(word.replace("'", "''"))
    return f"{word}:*"


def relaxed_tsquery_text(search_text: str | None) -> str | None:
    """OR-relaxed tsquery expression for a failed strict query, or None."""
    words = relaxed_query_words(search_text)
    if not words:
        return None
    return " | ".join(_relaxed_tsquery_term(word) for word in words)


def is_tsquery_syntax_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "syntax error in tsquery" in msg
        or "invalid input syntax for type tsquery" in msg
        or "no operand in tsquery" in msg
        or "no operator in tsquery" in msg
    )


# --- Filter compilation ---


def document_fts_vector_sql(
    processed_texts: Sequence[str], params: dict[str, Any], *, indexed_operands: bool = False
) -> str:
    """Build a query-sized vector representing lexemes found anywhere in one item."""
    operands: dict[str, str] = {}
    for processed_text in processed_texts:
        for operand, representative in _tsquery_operands(processed_text):
            operands.setdefault(operand, representative)

    present_lexemes: list[str] = []
    for index, (operand, representative) in enumerate(operands.items()):
        operand_param = f"text_operand_{index}"
        representative_param = f"text_representative_{index}"
        params[operand_param] = operand
        params[representative_param] = representative
        if indexed_operands:
            present_lexemes.append(
                f"CASE WHEN fts_candidate.operand_{index} THEN :{representative_param} ELSE '' END"
            )
            continue
        present_lexemes.append(
            "CASE WHEN (search_index.textsearchable_index_col "
            f"@@ to_tsquery('english', :{operand_param}) OR EXISTS ("
            "SELECT 1 FROM search_index_fts_chunks AS operand_chunk "
            "WHERE operand_chunk.project_id = search_index.project_id "
            "AND operand_chunk.search_index_id = search_index.id "
            "AND operand_chunk.search_index_type = search_index.type "
            "AND operand_chunk.textsearchable_index_col "
            f"@@ to_tsquery('english', :{operand_param}))) "
            f"THEN :{representative_param} ELSE '' END"
        )

    if not present_lexemes:
        return "search_index.textsearchable_index_col"

    # The synthesized text contains at most the query operands, never the note body.
    # This preserves document-wide Boolean semantics without recreating an
    # unbounded vector.
    lexeme_array = f"ARRAY[{', '.join(present_lexemes)}]"
    return f"to_tsvector('english', array_to_string({lexeme_array}, ' '))"


def _word_operand_from_clause(
    scope: ProjectScope, params: dict[str, Any], operands: Sequence[str]
) -> str:
    """Find document-wide operand presence through GIN, without detoasting each candidate.

    Each operand contributes document keys from the parent and chunk indexes. Grouping
    those keys preserves cross-chunk AND/NOT semantics and operand order for ranking.
    Unindexable negation additionally admits every scoped row, including documents with
    none of the operands, just like the ordinary candidate path.
    """
    scans: list[str] = []
    presence: list[str] = []
    for index, operand in enumerate(operands):
        params[f"text_operand_{index}"] = operand
        for table, id_column, type_column in (
            ("search_index", "id", "type"),
            ("search_index_fts_chunks", "search_index_id", "search_index_type"),
        ):
            scans.append(
                f"SELECT project_id, {id_column} AS id, {type_column} AS type, "
                f"{index} AS operand FROM {table} "
                f"WHERE {scope.predicate('project_id', params)} "
                f"AND textsearchable_index_col @@ to_tsquery('english', :text_operand_{index})"
            )
        presence.append(f"bool_or(operand = {index}) AS operand_{index}")
    scans.append(
        "SELECT project_id, id, type, -1 AS operand FROM search_index "
        f"WHERE {scope.predicate('project_id', params)} "
        "AND querytree(to_tsquery('english', :text)) = 'T'"
    )
    return (
        "search_index JOIN (SELECT project_id, id, type, "
        f"{', '.join(presence)} FROM ({' UNION ALL '.join(scans)}) AS operand_matches "
        "GROUP BY project_id, id, type) AS fts_candidate "
        "ON fts_candidate.project_id = search_index.project_id "
        "AND fts_candidate.id = search_index.id AND fts_candidate.type = search_index.type"
    )


def _word_candidate_from_clause(scope: ProjectScope, params: dict[str, Any]) -> str:
    """Join search rows to the GIN-indexed candidates for the word channel.

    Trigger: PostgreSQL can extract a required-positive query tree.
    Why: OR-ing its operands is a safe indexed superset even when terms live in
    different chunks. Pure or optional negation returns ``T`` and must retain all
    scoped rows for correct semantics.
    Outcome: ordinary and required-positive NOT queries use both GIN indexes; only
    genuinely unindexable negation scans the scope.
    """
    parent_scope = scope.predicate("candidate_parent.project_id", params)
    chunk_scope = scope.predicate("candidate_chunk.project_id", params)
    all_scope = scope.predicate("candidate_all.project_id", params)
    return f"""
        search_index JOIN (
            SELECT
                candidate_parent.project_id,
                candidate_parent.id,
                candidate_parent.type
            FROM search_index AS candidate_parent
            WHERE {parent_scope}
              AND querytree(to_tsquery('english', :text)) <> 'T'
              AND candidate_parent.textsearchable_index_col
                  @@ to_tsquery('english', :text_candidate)
            UNION
            SELECT
                candidate_chunk.project_id,
                candidate_chunk.search_index_id AS id,
                candidate_chunk.search_index_type AS type
            FROM search_index_fts_chunks AS candidate_chunk
            WHERE {chunk_scope}
              AND querytree(to_tsquery('english', :text)) <> 'T'
              AND candidate_chunk.textsearchable_index_col
                  @@ to_tsquery('english', :text_candidate)
            UNION
            SELECT
                candidate_all.project_id,
                candidate_all.id,
                candidate_all.type
            FROM search_index AS candidate_all
            WHERE {all_scope}
              AND querytree(to_tsquery('english', :text)) = 'T'
        ) AS fts_candidate
          ON fts_candidate.project_id = search_index.project_id
         AND fts_candidate.id = search_index.id
         AND fts_candidate.type = search_index.type
    """


def _script_candidate_from_clause(scope: ProjectScope, params: dict[str, Any]) -> str:
    """Join search rows to the GIN-indexed candidates for the script channel.

    Trigger: a query contains script grams, with or without word terms.
    Why: every script phrase is required, while an English word clause can reduce
    to an empty tsquery after dictionary processing.
    Outcome: start from the parent and child script GIN indexes, then apply every
    word and script predicate.
    """
    parent_scope = scope.predicate("script_parent.project_id", params)
    chunk_scope = scope.predicate("script_candidate.project_id", params)
    return f"""
        search_index JOIN (
            SELECT
                script_parent.project_id,
                script_parent.id,
                script_parent.type
            FROM search_index AS script_parent
            WHERE {parent_scope}
              AND script_parent.script_ngrams_index_col
                  @@ to_tsquery('simple', :script_candidate_text)
            UNION
            SELECT
                script_candidate.project_id,
                script_candidate.search_index_id AS id,
                script_candidate.search_index_type AS type
            FROM search_index_fts_chunks AS script_candidate
            WHERE {chunk_scope}
              AND script_candidate.script_ngrams_index_col
                  @@ to_tsquery('simple', :script_candidate_text)
        ) AS fts_candidate
          ON fts_candidate.project_id = search_index.project_id
         AND fts_candidate.id = search_index.id
         AND fts_candidate.type = search_index.type
    """


def compile_fts_filter(
    scope: ProjectScope,
    query: PreparedSearchQuery,
    *,
    allow_relaxed: bool = False,
    candidate_keys: Sequence[SearchIndexKey] | None = None,
) -> CompiledFilter:
    """Compile Postgres FTS FROM/WHERE/score shared by search and count.

    ``allow_relaxed`` widens the indexed candidate set to the relaxed query's
    operands too, so the strict statement and its relaxed retry read the same rows.
    """
    params: dict[str, Any] = {}
    shared_conditions = shared_filter_conditions(
        scope, params, dialect=POSTGRES_FILTER_DIALECT, query=query, candidate_keys=candidate_keys
    )
    conditions: list[str] = []
    from_clause = "search_index"
    document_vector: str | None = None
    indexed_document_vector: str | None = None
    script_tsqueries: list[str] = []
    search_text = query.search_text

    # Wildcard-only and blank text add no text condition: every row matches.
    if search_text and search_text.strip() not in ("", "*"):
        script_query = analyze_script_query(search_text.strip())
        if script_query.word_text:
            processed_text = prepare_search_term(script_query.word_text)
            params["text"] = processed_text
            probe_texts = [processed_text]
            if allow_relaxed:
                relaxed_text = relaxed_tsquery_text(script_query.word_text)
                if relaxed_text:
                    probe_texts.append(relaxed_text)

            candidate_operands: dict[str, None] = {}
            for probe_text in probe_texts:
                for operand, _representative in _tsquery_operands(probe_text):
                    candidate_operands.setdefault(operand, None)
            if candidate_operands:
                params["text_candidate"] = " | ".join(candidate_operands)
                from_clause = _word_candidate_from_clause(scope, params)
            # Multi-word queries otherwise detoast the same candidate's chunks once
            # per operand, again for ranking, and again for count. Collect presence
            # from GIN instead. Single-word and mixed-script queries keep their
            # existing candidate path and parent short-circuit behavior.
            # Each indexed operand adds a projected column and two UNION branches.
            # Bound query width well below PostgreSQL's 1664-column limit; longer
            # pasted queries retain the existing single-vector candidate path.
            if (
                1 < len(candidate_operands) <= _MAX_INDEXED_OPERANDS
                and not script_query.gram_phrases
            ):
                from_clause = _word_operand_from_clause(scope, params, list(candidate_operands))
                indexed_document_vector = document_fts_vector_sql(
                    probe_texts, params, indexed_operands=True
                )
                document_vector = "search_index.document_fts_vector"
            else:
                document_vector = document_fts_vector_sql(probe_texts, params)
            word_condition = f"{document_vector} @@ to_tsquery('english', :text)"
            if script_query.gram_phrases:
                # Trigger: PostgreSQL's English dictionary removes every word term.
                # Why: an empty word query must not suppress a required script match.
                # Outcome: only mixed queries treat the empty word channel as neutral;
                # word-only stopword queries retain their established empty result.
                word_condition = f"(numnode(to_tsquery('english', :text)) = 0 OR {word_condition})"
            conditions.append(word_condition)

        if script_query.gram_phrases:
            script_tsqueries = [
                " <-> ".join(f"'{gram}'" for gram in phrase) for phrase in script_query.gram_phrases
            ]
            for index, script_tsquery in enumerate(script_tsqueries):
                params[f"script_text_{index}"] = script_tsquery
            params["script_candidate_text"] = " | ".join(
                f"({script_tsquery})" for script_tsquery in script_tsqueries
            )
            from_clause = _script_candidate_from_clause(scope, params)
            conditions.extend(
                "(search_index.script_ngrams_index_col "
                f"@@ to_tsquery('simple', :script_text_{index}) OR EXISTS ("
                "SELECT 1 FROM search_index_fts_chunks AS script_chunk "
                "WHERE script_chunk.project_id = search_index.project_id "
                "AND script_chunk.search_index_id = search_index.id "
                "AND script_chunk.search_index_type = search_index.type "
                "AND script_chunk.script_ngrams_index_col "
                f"@@ to_tsquery('simple', :script_text_{index})))"
                for index in range(len(script_tsqueries))
            )

    # --- Fence the text candidates (#1791) ---
    # Trigger: a GIN candidate join narrows the rows text conditions must check.
    # Why: the planner estimates one row from the candidate UNION, so it applied the
    #   text conditions, a per-operand EXISTS into the chunk table, to every row in
    #   scope before joining the candidates. On an 8k-note project that was about
    #   17k chunk probes and 700k buffer touches per query.
    # Outcome: OFFSET 0 keeps the joined candidates, already narrowed by the cheap
    #   scope, type and key filters, as their own subquery, so the text conditions
    #   run only on the candidates. The alias keeps every later reference to
    #   ``search_index`` unchanged.
    candidate_join = from_clause != "search_index"
    if candidate_join:
        projection = "search_index.*"
        if indexed_document_vector is not None:
            projection += f", {indexed_document_vector} AS document_fts_vector"
        from_clause = (
            f"(SELECT {projection} FROM {from_clause} "
            f"WHERE {' AND '.join(shared_conditions)} OFFSET 0) AS search_index"
        )
    else:
        conditions = [*shared_conditions, *conditions]

    leading_order_by = ""
    if query.title:
        params["title_text"] = prepare_search_term(query.title.strip(), is_prefix=False)
        conditions.append(
            "to_tsvector('english', search_index.title) @@ to_tsquery('english', :title_text)"
        )
        # A title-only search has no rank, so without this key matching notes come
        # back in id order; an exact title match sorts first, as on SQLite (#1956).
        params["title_exact"] = query.title.strip()
        leading_order_by = "(lower(search_index.title) = lower(:title_exact)) DESC, "

    if query.permalink_match:
        permalink_text = query.permalink_match.lower().strip()
        if "*" in query.permalink_match:
            # ``*`` becomes the LIKE wildcard.
            params["permalink"] = permalink_text.replace("*", "%")
            conditions.append("search_index.permalink LIKE :permalink")
        else:
            params["permalink"] = permalink_text
            conditions.append("search_index.permalink = :permalink")

    # Structured metadata filters use jsonb_extract_path_text() / jsonb_extract_path()
    # with parameterized path parts instead of #>> / #> with interpolated paths.
    if query.metadata_filters:
        parsed_filters = parse_metadata_filters(query.metadata_filters)
        from_clause = f"{from_clause} JOIN entity ON search_index.entity_id = entity.id"
        # Frontmatter filters answer for notes only; see
        # metadata_filter_content_type_condition for why every regular file would
        # otherwise satisfy a null predicate.
        conditions.append(metadata_filter_content_type_condition(params))
        metadata_expr = "entity.entity_metadata::jsonb"

        for idx, filt in enumerate(parsed_filters):
            path_param_names: list[str] = []
            for j, part in enumerate(filt.path_parts):
                path_param = f"meta_path_{idx}_{j}"
                params[path_param] = part
                path_param_names.append(f":{path_param}")
            path_args = ", ".join(path_param_names)
            text_expr = f"jsonb_extract_path_text({metadata_expr}, {path_args})"
            json_expr = f"jsonb_extract_path({metadata_expr}, {path_args})"

            # jsonb_extract_path_text returns SQL NULL both for a missing key and for
            # an explicit JSON null, the same two cases SQLite's json_extract
            # collapses, so the dialects answer ``{"owner": None}`` row for row.
            # ``= NULL`` is never true.
            if filt.op == "is_null":
                conditions.append(f"{text_expr} IS NULL")
                continue

            if filt.op == "is_not_null":
                conditions.append(f"{text_expr} IS NOT NULL")
                continue

            if filt.op == "eq":
                value_param = f"meta_val_{idx}"
                params[value_param] = filt.value
                conditions.append(f"{text_expr} = :{value_param}")
                continue

            if filt.op == "in":
                placeholders: list[str] = []
                for j, val in enumerate(filt.value):
                    value_param = f"meta_val_{idx}_{j}"
                    params[value_param] = val
                    placeholders.append(f":{value_param}")
                conditions.append(f"{text_expr} IN ({', '.join(placeholders)})")
                continue

            if filt.op == "contains":
                base_param = f"meta_val_{idx}"
                tag_conditions: list[str] = []
                # Every requested value must be present.
                for j, val in enumerate(filt.value):
                    tag_param = f"{base_param}_{j}"
                    params[tag_param] = json.dumps([val])
                    # The exact JSONB containment test is the primary path; the
                    # substring patterns only reach values stored as array text.
                    like_condition = metadata_contains_like_condition(
                        text_expr,
                        val,
                        param_prefix=tag_param,
                        params=params,
                    )
                    tag_conditions.append(
                        f"({json_expr} @> CAST(:{tag_param} AS jsonb) OR {like_condition})"
                    )
                conditions.append(" AND ".join(tag_conditions))
                continue

            if filt.op in {"gt", "gte", "lt", "lte", "between"}:
                compare_expr = (
                    f"{text_expr}::double precision" if filt.comparison == "numeric" else text_expr
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

    # ts_rank per channel. With no text search there is no rank, so the score is 0.
    score_parts: list[str] = []
    if document_vector is not None:
        score_parts.append(
            "GREATEST("
            f"ts_rank({document_vector}, to_tsquery('english', :text)), "
            "ts_rank(search_index.textsearchable_index_col, to_tsquery('english', :text)), "
            "COALESCE((SELECT MAX(ts_rank("
            "fts_chunk.textsearchable_index_col, to_tsquery('english', :text))) "
            "FROM search_index_fts_chunks AS fts_chunk "
            "WHERE fts_chunk.project_id = search_index.project_id "
            "AND fts_chunk.search_index_id = search_index.id "
            "AND fts_chunk.search_index_type = search_index.type "
            "AND fts_chunk.textsearchable_index_col "
            "@@ to_tsquery('english', :text)), 0))"
        )
    score_parts.extend(
        "GREATEST("
        "ts_rank(search_index.script_ngrams_index_col, "
        f"to_tsquery('simple', :script_text_{index})), "
        "COALESCE((SELECT MAX(ts_rank(script_rank.script_ngrams_index_col, "
        f"to_tsquery('simple', :script_text_{index}))) "
        "FROM search_index_fts_chunks AS script_rank "
        "WHERE script_rank.project_id = search_index.project_id "
        "AND script_rank.search_index_id = search_index.id "
        "AND script_rank.search_index_type = search_index.type "
        "AND script_rank.script_ngrams_index_col "
        f"@@ to_tsquery('simple', :script_text_{index})), 0))"
        for index in range(len(script_tsqueries))
    )
    # Each condition above is required, so every query component contributes to
    # relevance. Taking only the strongest rank would make additional script runs
    # invisible.
    score_expression = " + ".join(score_parts) if score_parts else "0"

    return CompiledFilter(
        from_clause=from_clause,
        where_clause=" AND ".join(conditions),
        params=params,
        order_by_clause=AFTER_DATE_ORDER_BY if query.after_date else "",
        score_expression=score_expression,
        leading_order_by=leading_order_by,
        prefer_index_scans=candidate_join,
    )


# --- Execution ---


async def execute_fts_statement(
    session: AsyncSession,
    statement: str,
    params: dict[str, Any],
    *,
    prefer_index_scans: bool,
    use_savepoint: bool,
) -> Sequence[Row[Any]]:
    """Run one FTS statement and fetch its rows, steering text candidates to GIN (#1791).

    Trigger: the statement joins GIN text candidates (``prefer_index_scans``).
    Why: chunk ``tsvector``s live in TOAST, which the planner does not cost. It priced
      a sequential scan of ``search_index_fts_chunks`` at the heap's size and chose it
      over the GIN index, then detoasted every chunk to test it: 375 MB per query on
      a 95k-chunk project. A GIN bitmap reads only matching rows and never detoasts;
      it won at every selectivity measured, even with all chunks matching (921
      buffers against 481k).
    Outcome: sequential scans are off for this statement only. Inside a savepoint the
      prior value is restored before the savepoint is released, and a rolled-back
      savepoint reverts it on its own, so a caller's session plans normally afterwards.
      Without a savepoint the session is FTS's own, and the setting ends with its
      transaction.
    """

    async def run() -> Sequence[Row[Any]]:
        if not prefer_index_scans:
            return (await session.execute(text(statement), params)).fetchall()
        previous = (
            await session.execute(text("SELECT current_setting('enable_seqscan')"))
        ).scalar_one()
        await session.execute(text("SELECT set_config('enable_seqscan', 'off', true)"))
        rows = (await session.execute(text(statement), params)).fetchall()
        await session.execute(
            text("SELECT set_config('enable_seqscan', :previous, true)"),
            {"previous": previous},
        )
        return rows

    if use_savepoint:
        async with session.begin_nested():
            return await run()
    return await run()


class PostgresFts:
    """Run tsquery statements for any scope in one database."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

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
        with_text: bool = True,
    ) -> list[SearchIndexRow]:
        """Run one tsquery page, retrying a malformed or empty strict query relaxed."""
        search_text = query.search_text
        compiled = compile_fts_filter(
            scope, query, allow_relaxed=allow_relaxed, candidate_keys=candidate_keys
        )
        params = compiled.params
        params["limit"] = limit
        params["offset"] = offset
        text_columns = (
            capped_snippet_columns("search_index")
            if with_text
            else snippet_length_columns("search_index")
        )

        sql = f"""
            SELECT{_result_columns(text_columns)},
                {compiled.score_expression} as score
            FROM {compiled.from_clause}
            WHERE {compiled.where_clause}
            ORDER BY {compiled.leading_order_by}score DESC {compiled.order_by_clause},
                search_index.id ASC
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")
        fts_started_at = time.perf_counter() if trace is not None else None

        use_savepoint = session is not None or allow_relaxed

        async def execute_rows(active_session: AsyncSession, query_params: dict[str, Any]):
            # PostgreSQL leaves a transaction unusable after invalid tsquery syntax.
            # Scope retryable or caller-owned attempts to a savepoint so a relaxed
            # retry, and any caller continuing to use its session, starts healthy.
            return await execute_fts_statement(
                active_session,
                sql,
                query_params,
                prefer_index_scans=compiled.prefer_index_scans,
                use_savepoint=use_savepoint,
            )

        async def run_search(active_session: AsyncSession):
            relaxed = relaxed_tsquery_text(search_text) if allow_relaxed else None
            strict_syntax_error = False
            relaxed_fallback_used = False
            try:
                rows = await execute_rows(active_session, params)
            except Exception as exc:
                if not (is_tsquery_syntax_error(exc) and relaxed and params.get("text")):
                    raise
                strict_syntax_error = True
                rows = []

            # Trigger: multi-word natural-language query matched nothing under the
            # default all-terms-AND tsquery semantics, or its punctuation produced
            # invalid strict tsquery syntax.
            # Why: questions rarely have every word in one document; without
            # relaxation the FTS half of hybrid search contributes zero candidates.
            # The relaxed renderer also tokenizes punctuation safely.
            # Outcome: one retry with OR-joined prefix lexemes; ts_rank still ranks
            # multi-term matches first.
            if relaxed and not rows and params.get("text"):
                relaxed_fallback_used = True
                retry_reason = "invalid syntax" if strict_syntax_error else "0 results"
                logger.debug(
                    f"Strict Postgres FTS returned {retry_reason}; retrying relaxed FTS query "
                    f"strict='{search_text}' relaxed='{relaxed}'"
                )
                with logfire.span(
                    "search.relaxed_fts_retry",
                    backend="postgres",
                    reason="syntax_error" if strict_syntax_error else "empty_result",
                    token_count=len(relaxed_query_words(search_text) or ()),
                    limit=limit,
                    offset=offset,
                ):
                    rows = await execute_rows(active_session, {**params, "text": relaxed})
            return rows, relaxed_fallback_used

        try:
            if session is not None:
                rows, relaxed_fallback_used = await run_search(session)
            else:
                async with db.scoped_session(self._session_maker) as owned_session:
                    rows, relaxed_fallback_used = await run_search(owned_session)
        except Exception as e:
            if is_tsquery_syntax_error(e):
                logger.warning(f"tsquery syntax error for search term: {search_text}, error: {e}")
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

    async def row_filter(
        self, scope: ProjectScope, query: PreparedSearchQuery
    ) -> tuple[str, dict[str, Any]]:
        """Return the query's structured filters as a subquery over search rows."""
        if query.search_text:
            raise ValueError("A row filter applies structured filters only; it takes no text.")
        compiled = compile_fts_filter(scope, query)
        sql = (
            "SELECT search_index.project_id, search_index.type, search_index.id "
            f"FROM {compiled.from_clause} WHERE {compiled.where_clause}"
        )
        return sql, compiled.params

    async def admitted_keys(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        candidate_keys: Sequence[SearchIndexKey],
    ) -> set[SearchIndexKey]:
        """Return which of ``candidate_keys`` the query admits, reading keys only."""
        compiled = compile_fts_filter(scope, query, candidate_keys=candidate_keys)
        sql = (
            "SELECT search_index.type, search_index.id "
            f"FROM {compiled.from_clause} WHERE {compiled.where_clause}"
        )
        async with db.scoped_session(self._session_maker) as session:
            result = await session.execute(text(sql), compiled.params)
            return {(row.type, row.id) for row in result.fetchall()}

    async def count(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        *,
        allow_relaxed: bool = False,
    ) -> int:
        """Count rows matching the tsquery, with the same relaxed retry as search."""
        search_text = query.search_text
        compiled = compile_fts_filter(scope, query, allow_relaxed=allow_relaxed)
        params = compiled.params
        sql = f"SELECT COUNT(*) FROM {compiled.from_clause} WHERE {compiled.where_clause}"
        logger.trace(f"Count {sql} params: {params}")

        async def execute_count(active_session: AsyncSession, query_params: dict[str, Any]) -> int:
            rows = await execute_fts_statement(
                active_session,
                sql,
                query_params,
                prefer_index_scans=compiled.prefer_index_scans,
                use_savepoint=allow_relaxed,
            )
            return int(rows[0][0])

        try:
            async with db.scoped_session(self._session_maker) as session:
                relaxed = relaxed_tsquery_text(search_text) if allow_relaxed else None
                strict_syntax_error = False
                try:
                    total = await execute_count(session, params)
                except Exception as exc:
                    if not (is_tsquery_syntax_error(exc) and relaxed and params.get("text")):
                        raise
                    strict_syntax_error = True
                    total = 0

                if relaxed and total == 0 and params.get("text"):
                    with logfire.span(
                        "search.count.relaxed_fts_retry",
                        backend="postgres",
                        reason="syntax_error" if strict_syntax_error else "empty_result",
                        token_count=len(relaxed_query_words(search_text) or ()),
                    ):
                        total = await execute_count(session, {**params, "text": relaxed})
                return total
        except Exception as e:
            if is_tsquery_syntax_error(e):
                logger.warning(f"tsquery syntax error for search term: {search_text}, error: {e}")
                return 0
            logger.error(f"Database error during search count: {e}")
            raise
