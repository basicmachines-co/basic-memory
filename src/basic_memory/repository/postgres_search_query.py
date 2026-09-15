"""Read-only FTS query compilation for explicit project scopes."""

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.repository.search_query import relaxed_query_words, relaxation_word_tokens
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.search_repository_base import (
    SearchIndexKey,
    candidate_key_restriction_condition,
    file_path_prefix_condition,
    metadata_contains_like_condition,
    metadata_filter_content_type_condition,
)
from basic_memory.repository.metadata_filters import parse_metadata_filters
from basic_memory.repository.note_type_filters import (
    POSTGRES_NOTE_TYPE_VALUE,
    build_note_type_predicate,
)
from basic_memory.repository.temporal_filters import build_temporal_predicate
from basic_memory.schemas.search import SearchItemType
from basic_memory.temporal import TemporalFilter


_TSQUERY_OPERAND_PATTERN = re.compile(r"'(?:''|[^'])*'(?::\*)?|[^\s&|!()]+")
_TSQUERY_WORD_PATTERN = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
_QUOTED_QUERY_PATTERN = re.compile(r'"([^"]*)"')
_BOOLEAN_WORDS = frozenset({"AND", "OR", "NOT"})
_TSQUERY_METACHARACTERS = frozenset("&|!:<>")


def _tsquery_operands(processed_text: str) -> list[tuple[str, str]]:
    """Return unique (query operand, representative text) pairs in source order."""
    operands: dict[str, str] = {}
    for operand in _TSQUERY_OPERAND_PATTERN.findall(processed_text):
        representative = operand.removesuffix(":*")
        if representative.startswith("'") and representative.endswith("'"):
            representative = representative[1:-1].replace("''", "'")
            operands.setdefault(operand, representative)
            continue

        # An unquoted apostrophe is invalid tsquery syntax. Keep the literal
        # word for the synthetic document, but quote and escape its probe so a
        # strict syntax failure can proceed to the relaxed retry.
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

        # A malformed strict operand (for example ``foo<bar:*``) must not poison
        # the later relaxed retry. Split punctuation into the same safe word pieces
        # that PostgreSQL will lex, while the original full tsquery still determines
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

    operands = []
    for word in words:
        escaped_word = "'{}'".format(word.replace("'", "''")) if "'" in word else word
        operands.append(f"{escaped_word}:*" if is_prefix else escaped_word)
    return operator.join(operands)


def _render_boolean_operand(operand: str) -> str:
    """Preserve safe structured text while escaping tsquery syntax bytes."""
    if "'" not in operand and not any(char in _TSQUERY_METACHARACTERS for char in operand):
        return operand
    return _render_tsquery_words(
        operand,
        operator=" & ",
        is_prefix=False,
    )


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


class PostgresSearchQuery:
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
        if '"' in term or any(op in f" {term} " for op in boolean_operators):
            return self._prepare_boolean_query(term)

        # For non-Boolean queries, prepare single term
        return self._prepare_single_term(term, is_prefix)

    @staticmethod
    def _relaxed_tsquery_term(word: str) -> str:
        """Render one relaxed word as a tsquery-safe prefix expression.

        Mirrors the SQLite renderer: a word token can contain an apostrophe, and
        tsquery reads that as lexeme-quoting syntax rather than text. Quoting the
        lexeme and doubling any interior quote keeps it literal.
        """
        if "'" in word:
            return "'{}':*".format(word.replace("'", "''"))
        return f"{word}:*"

    @staticmethod
    def _relaxed_tsquery_text(search_text: Optional[str]) -> Optional[str]:
        """OR-relaxed tsquery expression for a failed strict query, or None."""
        words = relaxed_query_words(search_text)
        if not words:
            return None
        return " | ".join(PostgresSearchQuery._relaxed_tsquery_term(word) for word in words)

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
        # PostgreSQL's strict to_tsquery grammar does not accept web-style double
        # quotes. Convert complete quoted groups first so operator-looking words
        # inside them remain text and every word becomes a complete operand.
        quoted_phrases: dict[str, str] = {}

        def replace_quoted_phrase(match: re.Match[str]) -> str:
            phrase = _render_tsquery_words(
                match.group(1),
                operator=" & ",
                is_prefix=False,
            )
            placeholder = f"BMQUOTEDPHRASE{len(quoted_phrases)}"
            while placeholder in query:
                placeholder += "X"
            quoted_phrases[placeholder] = f"({phrase})"
            # Surround the placeholder so quotes adjacent to plain text become
            # explicit operands instead of restoring into ``word(group)``.
            return f" {placeholder} "

        result = _QUOTED_QUERY_PATTERN.sub(replace_quoted_phrase, query)
        if '"' in result:
            # An unmatched quote is user text, not a reason to abort the database
            # transaction. Boolean-looking words lose their operator role here.
            return _render_tsquery_words(
                query,
                operator=" & ",
                is_prefix=True,
                drop_boolean_words=True,
            )

        # Boolean syntax is the only structure retained from user input. A
        # whitespace-delimited operand still needs explicit conjunctions, but
        # PostgreSQL must tokenize structured single operands such as
        # ``auth-service`` and ``config.json`` exactly as it did before quoted
        # query normalization.
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
                    _render_tsquery_words(
                        stripped_part,
                        operator=" & ",
                        is_prefix=False,
                    )
                )
                continue
            normalized_parts.append(_render_boolean_operand(stripped_part))
        # Convert operators from the parsed sequence so ``A NOT B`` does not
        # depend on the final character of A. Structured operands such as C++
        # may end in punctuation but still require the conjunction before NOT.
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
            # Quotes are replaced before parentheses are tokenized, so adjacent
            # groups can arrive as separate operands without an explicit AND.
            # PostgreSQL requires that conjunction between an operand or closed
            # group and the next operand or opening group.
            if part != ")" and tsquery_parts and tsquery_parts[-1] not in {"&", "|", "!", "("}:
                tsquery_parts.append("&")
            tsquery_parts.append(part)
        result = " ".join(tsquery_parts)

        # Attach negation to its operand after structural conversion.
        result = re.sub(r"!\s+", "!", result)
        result = re.sub(r"\(\s+", "(", result)
        result = re.sub(r"\s+\)", ")", result)

        if not _has_valid_boolean_shape(result):
            # Keep the searchable words from malformed Boolean input while
            # removing the operators that made its structure incomplete.
            return _render_tsquery_words(
                query,
                operator=" & ",
                is_prefix=True,
                drop_boolean_words=True,
            )

        # A multi-digit placeholder contains shorter numeric placeholders as
        # prefixes, so restore longest names first to keep each token atomic.
        for placeholder in sorted(quoted_phrases, key=len, reverse=True):
            phrase = quoted_phrases[placeholder]
            result = result.replace(placeholder, phrase)

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
            # Strip sentence punctuation from word edges so question-form
            # queries produce clean lexemes (parity with SQLite FTS5 prep).
            # The tsquery tokenizer ignores this punctuation anyway; leaving it
            # in only risks tsquery syntax errors. Interior characters are kept.
            words = [w.strip("?!.,;") for w in cleaned_term.split()]
            words = [w for w in words if w]
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

        # Single word: strip edge punctuation; guard the now-empty case so a
        # bare ":*"/"" never reaches tsquery.
        cleaned_term = cleaned_term.strip().strip("?!.,;")
        if not cleaned_term:
            return "NOSPECIALCHARS:*"
        if is_prefix:
            return f"{cleaned_term}:*"
        else:
            return cleaned_term

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
        allow_relaxed: bool = False,
        candidate_keys: Sequence[SearchIndexKey] | None = None,
    ) -> tuple[str, str, dict[str, Any], str, str]:
        """Build Postgres FTS FROM/WHERE params shared by search and count."""
        conditions = []
        params: dict[str, Any] = dict(self._scope_params)
        order_by_clause = ""
        from_clause = "search_index"
        document_vector_sql: str | None = None
        script_tsqueries: list[str] = []

        # Handle text search for title and content using tsvector
        if search_text:
            if search_text.strip() == "*" or search_text.strip() == "":
                # For wildcard searches, don't add any text conditions
                pass
            else:
                script_query = analyze_script_query(search_text.strip())
                if script_query.word_text:
                    processed_text = self._prepare_search_term(script_query.word_text)
                    params["text"] = processed_text
                    probe_texts = [processed_text]
                    if allow_relaxed:
                        relaxed_text = self._relaxed_tsquery_text(script_query.word_text)
                        if relaxed_text:
                            probe_texts.append(relaxed_text)

                    candidate_operands: dict[str, None] = {}
                    for probe_text in probe_texts:
                        for operand, _representative in _tsquery_operands(probe_text):
                            candidate_operands.setdefault(operand, None)
                    if candidate_operands:
                        params["text_candidate"] = " | ".join(candidate_operands)

                        # Trigger: PostgreSQL can extract a required-positive query tree.
                        # Why: OR-ing its operands is a safe indexed superset even when
                        # terms live in different chunks. Pure/optional negation returns
                        # ``T`` and must retain all project rows for correct semantics.
                        # Outcome: ordinary and required-positive NOT queries use both
                        # GIN indexes; only genuinely unindexable negation scans the project.
                        from_clause = f"""
                            search_index JOIN (
                                SELECT
                                    candidate_parent.project_id,
                                    candidate_parent.id,
                                    candidate_parent.type
                                FROM search_index AS candidate_parent
                                WHERE candidate_parent.project_id {self._scope_sql}
                                  AND querytree(to_tsquery('english', :text)) <> 'T'
                                  AND candidate_parent.textsearchable_index_col
                                      @@ to_tsquery('english', :text_candidate)
                                UNION
                                SELECT
                                    candidate_chunk.project_id,
                                    candidate_chunk.search_index_id AS id,
                                    candidate_chunk.search_index_type AS type
                                FROM search_index_fts_chunks AS candidate_chunk
                                WHERE candidate_chunk.project_id {self._scope_sql}
                                  AND querytree(to_tsquery('english', :text)) <> 'T'
                                  AND candidate_chunk.textsearchable_index_col
                                      @@ to_tsquery('english', :text_candidate)
                                UNION
                                SELECT
                                    candidate_all.project_id,
                                    candidate_all.id,
                                    candidate_all.type
                                FROM search_index AS candidate_all
                                WHERE candidate_all.project_id {self._scope_sql}
                                  AND querytree(to_tsquery('english', :text)) = 'T'
                            ) AS fts_candidate
                              ON fts_candidate.project_id = search_index.project_id
                             AND fts_candidate.id = search_index.id
                             AND fts_candidate.type = search_index.type
                        """
                    document_vector_sql = self._document_fts_vector_sql(probe_texts, params)
                    word_condition = f"{document_vector_sql} @@ to_tsquery('english', :text)"
                    if script_query.gram_phrases:
                        # Trigger: PostgreSQL's English dictionary removes every word term.
                        # Why: an empty word query must not suppress a required script match.
                        # Outcome: only mixed queries treat the empty word channel as neutral;
                        # word-only stopword queries retain their established empty result.
                        word_condition = (
                            f"(numnode(to_tsquery('english', :text)) = 0 OR {word_condition})"
                        )
                    conditions.append(word_condition)

                if script_query.gram_phrases:
                    script_tsqueries = [
                        " <-> ".join(f"'{gram}'" for gram in phrase)
                        for phrase in script_query.gram_phrases
                    ]
                    for index, script_tsquery in enumerate(script_tsqueries):
                        params[f"script_text_{index}"] = script_tsquery
                    # Trigger: a query contains script grams, with or without word terms.
                    # Why: every script phrase is required, while an English word clause can
                    # reduce to an empty tsquery after dictionary processing.
                    # Outcome: start from the parent and child script GIN indexes, then apply
                    # every word and script predicate below.
                    params["script_candidate_text"] = " | ".join(
                        f"({script_tsquery})" for script_tsquery in script_tsqueries
                    )
                    from_clause = f"""
                        search_index JOIN (
                            SELECT
                                script_parent.project_id,
                                script_parent.id,
                                script_parent.type
                            FROM search_index AS script_parent
                            WHERE script_parent.project_id {self._scope_sql}
                              AND script_parent.script_ngrams_index_col
                                  @@ to_tsquery('simple', :script_candidate_text)
                            UNION
                            SELECT
                                script_candidate.project_id,
                                script_candidate.search_index_id AS id,
                                script_candidate.search_index_type AS type
                            FROM search_index_fts_chunks AS script_candidate
                            WHERE script_candidate.project_id {self._scope_sql}
                              AND script_candidate.script_ngrams_index_col
                                  @@ to_tsquery('simple', :script_candidate_text)
                        ) AS fts_candidate
                          ON fts_candidate.project_id = search_index.project_id
                         AND fts_candidate.id = search_index.id
                         AND fts_candidate.type = search_index.type
                    """
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

        # Handle title search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            conditions.append(
                "to_tsvector('english', search_index.title) @@ to_tsquery('english', :title_text)"
            )

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("search_index.permalink = :permalink")

        # Handle permalink pattern match
        if permalink_match:
            permalink_text = permalink_match.lower().strip()
            params["permalink"] = permalink_text
            if "*" in permalink_match:
                # Use LIKE for pattern matching in Postgres
                # Convert * to % for SQL LIKE
                permalink_pattern = permalink_text.replace("*", "%")
                params["permalink"] = permalink_pattern
                conditions.append("search_index.permalink LIKE :permalink")
            else:
                conditions.append("search_index.permalink = :permalink")

        # Handle directory subtree scope. The predicate is built by the shared
        # helper so Postgres and SQLite scope by the identical rule; see
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

        # Handle search item type filter (parameterized for defense-in-depth)
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
                    note_type_value=POSTGRES_NOTE_TYPE_VALUE,
                )
            )

        # Handle date filter
        if after_date:
            params["after_date"] = after_date
            # Filter on updated_at so recently-edited notes are included even when created_at is old
            conditions.append("search_index.updated_at > :after_date")
            # order by most recent first
            order_by_clause = ", search_index.updated_at DESC"

        # Handle authored valid time (SPEC-82).
        # Trigger: caller asked when a statement was true of the world.
        # Why: `after_date` above filters `updated_at`, which records when the note was
        #      last edited. That is bookkeeping, never a semantic claim; a decision
        #      effective through July says nothing about when its file was touched.
        # Outcome: an independent predicate over the temporal projection, textually
        #          identical to the SQLite one because canonical bounds compare
        #          lexicographically on both backends. Undated sources carry no row and
        #          are therefore excluded whenever a valid-time filter is present.
        if temporal is not None:
            conditions.append(
                build_temporal_predicate(temporal, params, project_scope_sql=self._scope_sql)
            )

        # Handle structured metadata filters (frontmatter)
        # Uses jsonb_extract_path_text() / jsonb_extract_path() with parameterized
        # path parts instead of #>> / #> with interpolated paths.
        if metadata_filters:
            parsed_filters = parse_metadata_filters(metadata_filters)
            from_clause = f"{from_clause} JOIN entity ON search_index.entity_id = entity.id"
            # Frontmatter filters answer for notes only; see
            # metadata_filter_content_type_condition for why every regular file
            # would otherwise satisfy a null predicate.
            conditions.append(metadata_filter_content_type_condition(params))
            metadata_expr = "entity.entity_metadata::jsonb"

            for idx, filt in enumerate(parsed_filters):
                # Parameterize each JSON path part individually
                path_param_names = []
                for j, part in enumerate(filt.path_parts):
                    path_param = f"meta_path_{idx}_{j}"
                    params[path_param] = part
                    path_param_names.append(f":{path_param}")
                path_args = ", ".join(path_param_names)
                text_expr = f"jsonb_extract_path_text({metadata_expr}, {path_args})"
                json_expr = f"jsonb_extract_path({metadata_expr}, {path_args})"

                # jsonb_extract_path_text returns SQL NULL both for a missing key
                # and for an explicit JSON null — the same two cases SQLite's
                # json_extract collapses — so the dialects answer
                # `{"owner": None}` row for row. `= NULL` is never true, so
                # equality here would report a confident zero.
                if filt.op == "is_null":
                    conditions.append(f"{text_expr} IS NULL")
                    continue

                if filt.op == "eq":
                    value_param = f"meta_val_{idx}"
                    params[value_param] = filt.value
                    conditions.append(f"{text_expr} = :{value_param}")
                    continue

                if filt.op == "in":
                    placeholders = []
                    for j, val in enumerate(filt.value):
                        value_param = f"meta_val_{idx}_{j}"
                        params[value_param] = val
                        placeholders.append(f":{value_param}")
                    conditions.append(f"{text_expr} IN ({', '.join(placeholders)})")
                    continue

                if filt.op == "contains":
                    base_param = f"meta_val_{idx}"
                    tag_conditions = []
                    # Require all values to be present
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
                        f"{text_expr}::double precision"
                        if filt.comparison == "numeric"
                        else text_expr
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

        conditions.append(f"search_index.project_id {self._scope_sql}")

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Build SQL with ts_rank() for scoring
        # Note: If no text search, score will be NULL, so we use COALESCE to default to 0
        score_parts: list[str] = []
        if document_vector_sql is not None:
            score_parts.append(
                "GREATEST("
                f"ts_rank({document_vector_sql}, to_tsquery('english', :text)), "
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
        # Each condition above is required, so every query component should contribute to
        # relevance. Taking only the strongest rank makes additional script runs invisible.
        score_expr = " + ".join(score_parts) if score_parts else "0"

        return from_clause, where_clause, params, order_by_clause, score_expr

    @staticmethod
    def _document_fts_vector_sql(processed_texts: Sequence[str], params: dict[str, Any]) -> str:
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
        # This preserves document-wide Boolean semantics without recreating an unbounded vector.
        lexeme_array = f"ARRAY[{', '.join(present_lexemes)}]"
        return f"to_tsvector('english', array_to_string({lexeme_array}, ' '))"
