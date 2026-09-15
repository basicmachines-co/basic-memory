"""WHERE-clause pieces both search backends share.

The two FTS engines differ in how they match text and read JSON. Every other filter a
search accepts asks the same question of the same columns on both, so it is compiled
once here. A backend supplies the two spellings that differ through ``FilterDialect``
and appends its own text, title, permalink-pattern, and metadata predicates around the
shared ones.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from basic_memory.repository.note_type_filters import (
    POSTGRES_NOTE_TYPE_VALUE,
    SQLITE_NOTE_TYPE_VALUE,
    build_note_type_predicate,
)
from basic_memory.repository.search_repository_base import (
    SearchIndexKey,
    candidate_key_restriction_condition,
    file_path_prefix_condition,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.temporal_filters import build_temporal_predicate
from basic_memory.schemas.search import SearchItemType
from basic_memory.temporal import TemporalFilter

# Newest edits first whenever the caller filtered on ``after_date``.
AFTER_DATE_ORDER_BY = ", search_index.updated_at DESC"


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


def shared_filter_conditions(
    scope: ProjectScope,
    params: dict[str, Any],
    *,
    dialect: FilterDialect,
    permalink: str | None,
    file_path_prefix: str | None,
    candidate_keys: Sequence[SearchIndexKey] | None,
    search_item_types: Sequence[SearchItemType] | None,
    categories: Sequence[str] | None,
    note_types: Sequence[str] | None,
    after_date: datetime | None,
    temporal: TemporalFilter | None,
) -> list[str]:
    """Compile the filters whose SQL is identical on both backends.

    Binds are added to ``params`` in place. The scope predicate comes first: it is the
    one filter every statement carries, and it is what keeps rows outside the caller's
    projects out of every candidate window.
    """
    conditions = [scope.predicate("search_index.project_id", params)]

    if permalink:
        params["permalink"] = permalink
        conditions.append("search_index.permalink = :permalink")

    # See file_path_prefix_condition for the subtree boundary and escaping rules.
    subtree_condition = file_path_prefix_condition(file_path_prefix, params)
    if subtree_condition is not None:
        conditions.append(subtree_condition)

    # See candidate_key_restriction_condition for why the vector filter pass asks
    # about its candidates rather than paging the filter's whole match set (#1431).
    if candidate_keys is not None:
        conditions.append(candidate_key_restriction_condition(candidate_keys, params))

    if search_item_types:
        type_placeholders: list[str] = []
        for index, item_type in enumerate(search_item_types):
            name = f"search_type_{index}"
            params[name] = item_type.value
            type_placeholders.append(f":{name}")
        conditions.append(f"search_index.type IN ({', '.join(type_placeholders)})")

    # Trigger: caller passed ``categories`` to scope observation results.
    # Why: ``entity_types=["observation"]`` only narrows to the observation row type;
    #      callers expect exact-category matching, not incidental text matches.
    # Outcome: only rows whose indexed category exactly equals a requested value
    #          survive (entities/relations have NULL category and are excluded).
    if categories:
        category_placeholders: list[str] = []
        for index, category in enumerate(categories):
            name = f"category_{index}"
            params[name] = category
            category_placeholders.append(f":{name}")
        conditions.append(f"search_index.category IN ({', '.join(category_placeholders)})")

    # The note type belongs to the note, but only its entity row carries the
    # frontmatter, so the predicate resolves through the owning note. See
    # note_type_filters for why reading it off each row excluded every non-entity row.
    if note_types:
        conditions.append(
            build_note_type_predicate(
                note_types, params, scope=scope, note_type_value=dialect.note_type_value
            )
        )

    # Filter on updated_at so recently edited notes are included even when created_at
    # is old. The matching ORDER BY lives in AFTER_DATE_ORDER_BY.
    if after_date:
        params["after_date"] = after_date
        conditions.append(dialect.after_date_condition)

    # Authored valid time (SPEC-82) is independent of ``after_date``: that one is
    # bookkeeping about the file, this one is a claim about the world. See
    # temporal_filters for the overlap rule and why the subquery is non-correlated.
    if temporal is not None:
        conditions.append(build_temporal_predicate(temporal, params, scope=scope))

    return conditions
