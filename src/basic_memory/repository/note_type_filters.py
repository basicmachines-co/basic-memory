"""SQL for the note-type search predicate.

A note type is a property of the *note*, not of the individual rows projected from it.
One markdown file becomes several search rows -- the entity itself, one per observation,
one per outgoing relation -- and only the entity row carries `metadata.note_type`, because
that is where the frontmatter lives. An observation row carries `metadata.tags`; a relation
row carries no metadata at all.

Reading the type off each row therefore answers "is this row an entity of type X?" when the
question asked was "does this row belong to a note of type X?". Those coincide for entity
rows and for nothing else, which is invisible until a filter selects non-entity rows -- as a
valid-time filter does, since authored time lives on observations (SPEC-82). The conjunction
of the two was unsatisfiable: every row admitted by the temporal predicate was excluded by
the note-type one.

Resolving through the owning note fixes that at the source. Every search row already carries
`entity_id`, and an entity row's own `id` equals it, so one membership test covers all three
row kinds without special-casing any of them and without copying the type onto rows that
would then have to be kept in step with the note's frontmatter.

The predicate is a *non-correlated* subquery for the reason `temporal_filters` documents at
length: SQLite's `search_index` is an FTS5 virtual table, and a correlated `EXISTS` beside a
`MATCH` makes SQLite refuse the statement outright. A non-correlated `IN` is evaluated once,
independently, and composes with every FTS shape in this repository while leaving bm25
ranking intact.

One builder serves both dialects. Only the JSON accessor differs, so that is the single
thing a backend supplies -- the rule itself lives here rather than being written out once
per backend and drifting.
"""

from __future__ import annotations

from typing import Any, Sequence

from basic_memory.schemas.search import SearchItemType

SEARCH_TABLE = "search_index"

# The alias the owning note's row carries inside the subquery.
_OWNER = "note_type_owner"

# Each dialect's expression for the owning note's frontmatter type.
SQLITE_NOTE_TYPE_VALUE = f"json_extract({_OWNER}.metadata, '$.note_type')"
POSTGRES_NOTE_TYPE_VALUE = f"{_OWNER}.metadata->>'note_type'"


def build_note_type_predicate(
    note_types: Sequence[str],
    params: dict[str, Any],
    *,
    note_type_value: str,
) -> str:
    """Build the WHERE-clause fragment restricting rows to notes of the given types.

    The stored type keeps the frontmatter's own casing (`Chapter`), while the filter is
    documented case-insensitive, so both sides are folded to lowercase.

    Binds are added to `params` in place, following the convention the surrounding FTS
    query builders already use. `project_id` is bound by the caller for the whole query.
    """
    placeholders = []
    for index, note_type in enumerate(note_types):
        name = f"note_type_{index}"
        params[name] = note_type.lower()
        placeholders.append(f":{name}")

    return (
        f"{SEARCH_TABLE}.entity_id IN (\n"
        f"  SELECT {_OWNER}.id\n"
        f"    FROM {SEARCH_TABLE} AS {_OWNER}\n"
        f"   WHERE {_OWNER}.type = '{SearchItemType.ENTITY.value}'\n"
        f"     AND {_OWNER}.project_id = :project_id\n"
        f"     AND LOWER({note_type_value}) IN ({', '.join(placeholders)}))"
    )
