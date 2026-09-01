"""SQL for the valid-time search predicate (SPEC-82).

One builder serves both dialects. That is not a coincidence to be maintained by
discipline -- it falls out of two decisions made upstream:

* `basic_memory.temporal` canonicalizes every bound to a fixed-width lexical form,
  so `<`, `>`, and `=` on plain text columns *are* chronological comparisons and no
  typed date bind is needed on either side.
* Inclusivity on the query side is known while the SQL is being built, and
  inclusivity on the stored side is a boolean column, so both fold into the SQL text.
  The only bound parameters are the two bound values, the role, and the axis -- each
  compared directly against a column, so PostgreSQL always infers their type and
  asyncpg never sees a bare untyped parameter.

The predicate is a *non-correlated* subquery, and that shape is load-bearing rather
than stylistic. SQLite's default word search emits an OR of per-column `MATCH`
predicates; adding a correlated `EXISTS` to that WHERE clause makes SQLite refuse the
statement outright ("unable to use function MATCH in the requested context"). A
non-correlated `IN` is evaluated independently and composes with every FTS shape in
this repository -- the OR-of-columns form, the table-level `MATCH` used for script
queries, the bm25-preserving derived table, and the rowid rewrite -- while leaving
bm25 ranking intact. It also needs no change to any `from_clause`.
"""

from __future__ import annotations

from typing import Any

from basic_memory.temporal import TemporalFilter, TemporalRange

TEMPORAL_INDEX_TABLE = "memory_time_index"

# No stored assertion can match, and no subquery needs to run to prove it.
_MATCHES_NOTHING = "1 = 0"


def _not_source_ends_before_window(window: TemporalRange) -> str | None:
    """Reject stored ranges that finish before the queried window begins.

    Returns None when the window is unbounded below, because then nothing can end
    before it starts and the whole conjunct is vacuous.
    """
    if window.lower is None:
        return None
    clauses = [
        # An unbounded stored upper end never terminates, so it can never be "before".
        f"{TEMPORAL_INDEX_TABLE}.upper_value IS NULL",
        f"{TEMPORAL_INDEX_TABLE}.upper_value > :tq_lower",
    ]
    if window.lower_inclusive:
        # The window owns its lower endpoint, so a stored range that closes on that
        # same endpoint still shares it.
        clauses.append(
            f"({TEMPORAL_INDEX_TABLE}.upper_value = :tq_lower "
            f"AND {TEMPORAL_INDEX_TABLE}.upper_inclusive)"
        )
    return f"({' OR '.join(clauses)})"


def _not_window_ends_before_source(window: TemporalRange) -> str | None:
    """Reject stored ranges that begin after the queried window ends.

    The mirror image of `_not_source_ends_before_window`; None when the window is
    unbounded above.
    """
    if window.upper is None:
        return None
    clauses = [
        f"{TEMPORAL_INDEX_TABLE}.lower_value IS NULL",
        f"{TEMPORAL_INDEX_TABLE}.lower_value < :tq_upper",
    ]
    if window.upper_inclusive:
        clauses.append(
            f"({TEMPORAL_INDEX_TABLE}.lower_value = :tq_upper "
            f"AND {TEMPORAL_INDEX_TABLE}.lower_inclusive)"
        )
    return f"({' OR '.join(clauses)})"


def build_temporal_predicate(temporal: TemporalFilter, params: dict[str, Any]) -> str:
    """Build the WHERE-clause fragment restricting search rows by authored valid time.

    Two intervals overlap exactly when neither lies entirely before the other, which
    is what the two helpers above assert. Containment of a single date or instant is
    the same question asked of the degenerate closed range `[p,p]`, so `valid_at` and
    `valid_overlaps` share this one implementation and cannot drift apart.

    The result matches only sources carrying a structured assertion: a note without a
    qualifier contributes no row here and is therefore excluded, which is the
    documented default for a valid-time query.

    Binds are added to `params` in place, following the convention already used by the
    surrounding FTS query builders.
    """
    window = temporal.window
    if window is not None and window.is_empty:
        # PostgreSQL: nothing overlaps the empty range, not even itself. Emitting a
        # false constant is both correct and cheaper than running the subquery.
        return _MATCHES_NOTHING

    conditions = [f"{TEMPORAL_INDEX_TABLE}.project_id = :project_id"]

    if temporal.role is not None:
        params["tq_role"] = temporal.role.value
        conditions.append(f"{TEMPORAL_INDEX_TABLE}.time_role = :tq_role")

    if window is not None:
        # Trigger: the caller asked about a specific date or a specific instant.
        # Why: calendar dates and instants are different axes; converting between
        #   them would invent a timezone or a time of day the author never wrote.
        # Outcome: a date query can never match an instant range, or the reverse.
        params["tq_kind"] = window.kind.value
        conditions.append(f"{TEMPORAL_INDEX_TABLE}.range_kind = :tq_kind")
        # The empty stored range contains no points, so it overlaps nothing.
        conditions.append(f"NOT {TEMPORAL_INDEX_TABLE}.is_empty")

        if window.lower is not None:
            params["tq_lower"] = window.lower
        if window.upper is not None:
            params["tq_upper"] = window.upper
        conditions.extend(
            clause
            for clause in (
                _not_source_ends_before_window(window),
                _not_window_ends_before_source(window),
            )
            if clause is not None
        )

    where_clause = "\n     AND ".join(conditions)
    # (type, id) is the search row's own identity and the address this projection
    # stores, so the pair joins the two without a correlated reference.
    return (
        "(search_index.type, search_index.id) IN (\n"
        f"  SELECT {TEMPORAL_INDEX_TABLE}.source_type, {TEMPORAL_INDEX_TABLE}.source_id\n"
        f"    FROM {TEMPORAL_INDEX_TABLE}\n"
        f"   WHERE {where_clause})"
    )
