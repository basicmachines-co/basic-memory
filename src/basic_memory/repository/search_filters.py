"""WHERE-clause pieces both search backends share, and the FTS execution contract.

The two FTS engines differ in how they match text and read JSON. Every other filter a
search accepts asks the same question of the same columns on both, so it is compiled
once here. A backend supplies the two spellings that differ through ``FilterDialect``
and appends its own text, title, permalink-pattern, and metadata predicates around the
shared ones. ``FtsBackend`` is the narrow contract through which the shared read path
runs a compiled full-text statement on one engine.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.note_type_filters import (
    POSTGRES_NOTE_TYPE_VALUE,
    SQLITE_NOTE_TYPE_VALUE,
    build_note_type_predicate,
)
from basic_memory.repository.search_index_row import SearchIndexKey, SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import SearchTraceCollector
from basic_memory.repository.temporal_filters import build_temporal_predicate
from basic_memory.runtime.storage import RUNTIME_MARKDOWN_CONTENT_TYPE
from basic_memory.schemas.search import normalize_file_path_prefix

# Newest edits first whenever the caller filtered on ``after_date``.
AFTER_DATE_ORDER_BY = ", search_index.updated_at DESC"

# SQLite's LIKE has no default escape character, and Postgres's is already the
# backslash, so naming this one explicitly in every pattern is what lets a single
# escaped pattern mean the same thing on both backends.
_LIKE_ESCAPE_CHARACTER = "\\"


@dataclass(frozen=True, slots=True)
class FilterDialect:
    """The two SQL spellings that differ between backends inside the shared filters."""

    note_type_value: str
    after_date_condition: str


SQLITE_FILTER_DIALECT = FilterDialect(
    note_type_value=SQLITE_NOTE_TYPE_VALUE,
    # datetime() normalizes both sides so ISO strings of mixed precision compare as instants.
    after_date_condition="datetime(search_index.updated_at) > datetime(:after_date)",
)
POSTGRES_FILTER_DIALECT = FilterDialect(
    note_type_value=POSTGRES_NOTE_TYPE_VALUE,
    after_date_condition="search_index.updated_at > :after_date",
)


@dataclass(frozen=True, slots=True)
class CompiledFilter:
    """One backend's FROM, WHERE, and score for a search, ready to place in a statement.

    ``params`` is the bind dictionary the statement runs with. Callers add ``limit``
    and ``offset``, and a relaxed retry replaces ``text``.
    """

    from_clause: str
    where_clause: str
    params: dict[str, Any]
    order_by_clause: str
    score_expression: str


class FtsBackend(Protocol):
    """Run one engine's full-text statement for a scope and a prepared query.

    Both implementations compile through their own ``compile_fts_filter``, execute, and
    own the engine's failure semantics: FTS5 syntax errors answer with no rows, while
    Postgres retries a malformed strict tsquery through the relaxed renderer inside a
    savepoint so the caller's transaction survives.
    """

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
    ) -> list[SearchIndexRow]: ...

    async def count(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        *,
        allow_relaxed: bool = False,
    ) -> int: ...


# --- Filters both backends compile identically ---


def file_path_prefix_condition(
    file_path_prefix: str | None,
    params: dict[str, Any],
) -> str | None:
    """Build the SQL scoping search rows to one directory subtree of the project.

    One implementation, shared verbatim by both backends: a subtree scope that
    means different things on SQLite and Postgres would report an exact total
    for a match set the other dialect never produces.

    Boundary: the compared prefix carries its trailing separator, so "specs"
    admits "specs/api.md" and never "specs-archive/api.md".

    Why an explicit-length comparison rather than ``file_path LIKE 'specs/%'``:
    LIKE reads "_" and "%" as wildcards and both are ordinary characters in a
    directory name, so "my_notes" would silently also admit "my-notes"; and
    LIKE case-folds differently per backend, so one filter would answer two
    different questions. SUBSTR equality has no pattern language to escape and
    compares under each backend's deterministic default text collation, which is
    byte equality on both, so the dialects match exactly the same rows.
    """
    normalized = normalize_file_path_prefix(file_path_prefix)
    if normalized is None:
        return None
    prefix = f"{normalized}/"
    params["file_path_prefix"] = prefix
    params["file_path_prefix_length"] = len(prefix)
    return "SUBSTR(search_index.file_path, 1, :file_path_prefix_length) = :file_path_prefix"


def metadata_filter_content_type_condition(params: dict[str, Any]) -> str:
    """Build the SQL restricting a metadata-filtered query to Markdown notes.

    Frontmatter is a Markdown-only construct, but every indexed file (PDF, image,
    binary) gets its own ENTITY row whose ``entity_metadata`` carries no keys at all.
    A positive predicate can never match one, so this constraint was invisible until
    ``{"key": None}`` arrived: ``IS NULL`` is satisfied by the *absence* of a key,
    which is exactly the state every regular file is in, and the whole non-note half
    of a project counted into an exact total.

    Applied to any metadata filter, not just the null one, so the frontmatter-only
    contract is a property of the clause rather than of which operator happened to
    be used.
    """
    params["metadata_filter_content_type"] = RUNTIME_MARKDOWN_CONTENT_TYPE
    return "entity.content_type = :metadata_filter_content_type"


def metadata_contains_like_condition(
    extract_expr: str,
    value: Any,
    *,
    param_prefix: str,
    params: dict[str, Any],
) -> str:
    """Build the compatibility half of an array-contains metadata filter.

    The primary half of a ``{"tags": ["security"]}`` filter asks JSON whether the
    array holds the element (``json_each`` on SQLite, ``@>`` on Postgres) and answers
    only when the stored value really is a JSON array. Frontmatter written before
    tags were normalized can hold the array's *text* instead, either JSON-quoted
    ('["security", "auth"]') or as a Python repr ("['security', 'auth']"), and only a
    substring match finds an element inside those. Hence a pattern per quote style.

    LIKE reads "%" and "_" in the searched-for value as wildcards, so interpolating
    the value raw turned `tags has 100%` into a pattern that also matched
    "100-percent". Escaping both wildcards and the escape character itself makes the
    value literal again.
    """
    escaped = (
        str(value)
        .replace(_LIKE_ESCAPE_CHARACTER, _LIKE_ESCAPE_CHARACTER * 2)
        .replace("%", f"{_LIKE_ESCAPE_CHARACTER}%")
        .replace("_", f"{_LIKE_ESCAPE_CHARACTER}_")
    )
    double_quoted_param = f"{param_prefix}_like"
    single_quoted_param = f"{param_prefix}_like_single"
    params[double_quoted_param] = f'%"{escaped}"%'
    params[single_quoted_param] = f"%'{escaped}'%"
    escape_clause = f" ESCAPE '{_LIKE_ESCAPE_CHARACTER}'"
    return (
        f"{extract_expr} LIKE :{double_quoted_param}{escape_clause} "
        f"OR {extract_expr} LIKE :{single_quoted_param}{escape_clause}"
    )


def candidate_key_restriction_condition(
    candidate_keys: Sequence[SearchIndexKey],
    params: dict[str, Any],
) -> str:
    """Build the SQL restricting a filter query to an explicit set of search rows.

    This is what turns the vector/hybrid filter pass from "give me a page of everything
    the filter admits" into "of *these* candidates, which does the filter admit". The
    first question has an answer the size of the project and had to be capped, and every
    candidate outside the cap was then read as disallowed (#1431). The second question's
    answer is bounded by the candidate set itself, so no cap is needed and none of the
    candidates can fall off the end.

    Keys are grouped by row type rather than emitted as one ``(type, id)`` pair per
    branch: entity, observation, and relation ids come from independent sequences, so the
    type is part of the identity, but a handful of type-scoped ``IN`` lists binds one
    parameter per key instead of two and leaves the id list in the shape both planners
    can drive an index from. PostgreSQL's ``search_index`` primary key is
    ``(id, type, project_id)``.

    An empty candidate set is a real state, not a caller error (a vector search whose
    every hit was already dropped), and it admits nothing, so it yields a false
    predicate rather than the vacuous truth an empty ``OR`` would collapse to.
    """
    ids_by_type: dict[str, list[int]] = {}
    for row_type, row_id in candidate_keys:
        ids_by_type.setdefault(row_type, []).append(row_id)

    branches: list[str] = []
    for type_index, (row_type, row_ids) in enumerate(ids_by_type.items()):
        type_param = f"candidate_type_{type_index}"
        params[type_param] = row_type
        id_params: list[str] = []
        for id_index, row_id in enumerate(dict.fromkeys(row_ids)):
            id_param = f"candidate_id_{type_index}_{id_index}"
            params[id_param] = row_id
            id_params.append(f":{id_param}")
        branches.append(
            f"(search_index.type = :{type_param} AND search_index.id IN ({', '.join(id_params)}))"
        )

    if not branches:
        return "1 = 0"
    return f"({' OR '.join(branches)})"


def shared_filter_conditions(
    scope: ProjectScope,
    params: dict[str, Any],
    *,
    dialect: FilterDialect,
    query: PreparedSearchQuery,
    candidate_keys: Sequence[SearchIndexKey] | None,
) -> list[str]:
    """Compile the filters whose SQL is identical on both backends.

    Binds are added to ``params`` in place. The scope predicate comes first: it is the
    one filter every statement carries, and it is what keeps rows outside the caller's
    projects out of every candidate window.
    """
    conditions = [scope.predicate("search_index.project_id", params)]

    if query.permalink:
        params["permalink"] = query.permalink
        conditions.append("search_index.permalink = :permalink")

    subtree_condition = file_path_prefix_condition(query.file_path_prefix, params)
    if subtree_condition is not None:
        conditions.append(subtree_condition)

    if candidate_keys is not None:
        conditions.append(candidate_key_restriction_condition(candidate_keys, params))

    if query.search_item_types:
        type_placeholders: list[str] = []
        for index, item_type in enumerate(query.search_item_types):
            name = f"search_type_{index}"
            params[name] = item_type.value
            type_placeholders.append(f":{name}")
        conditions.append(f"search_index.type IN ({', '.join(type_placeholders)})")

    # Trigger: caller passed ``categories`` to scope observation results.
    # Why: ``entity_types=["observation"]`` only narrows to the observation row type;
    #      callers expect exact-category matching, not incidental text matches.
    # Outcome: only rows whose indexed category exactly equals a requested value
    #          survive (entities/relations have NULL category and are excluded).
    if query.categories:
        category_placeholders: list[str] = []
        for index, category in enumerate(query.categories):
            name = f"category_{index}"
            params[name] = category
            category_placeholders.append(f":{name}")
        conditions.append(f"search_index.category IN ({', '.join(category_placeholders)})")

    # The note type belongs to the note, but only its entity row carries the
    # frontmatter, so the predicate resolves through the owning note. See
    # note_type_filters for why reading it off each row excluded every non-entity row.
    if query.note_types:
        conditions.append(
            build_note_type_predicate(
                query.note_types, params, scope=scope, note_type_value=dialect.note_type_value
            )
        )

    # Filter on updated_at so recently edited notes are included even when created_at
    # is old. The matching ORDER BY lives in AFTER_DATE_ORDER_BY.
    if query.after_date:
        params["after_date"] = query.after_date
        conditions.append(dialect.after_date_condition)

    # Authored valid time (SPEC-82) is independent of ``after_date``: that one is
    # bookkeeping about the file, this one is a claim about the world. See
    # temporal_filters for the overlap rule and why the subquery is non-correlated.
    if query.temporal is not None:
        conditions.append(build_temporal_predicate(query.temporal, params, scope=scope))

    return conditions
