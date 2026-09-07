"""Schema resolver for Basic Memory.

Finds the applicable schema for a note using a priority-based resolution order:
  1. Inline schema  -> frontmatter['schema'] is a dict
  2. Explicit ref    -> frontmatter['schema'] is a string (entity name or permalink)
  3. Implicit by type -> frontmatter['type'] matches a schema note's entity field
  4. No schema       -> returns None (perfectly fine)

The resolver takes a search function as a dependency instead of importing
repository code directly, keeping the schema package decoupled from the
data access layer.
"""

from collections.abc import Callable, Awaitable
from dataclasses import dataclass

from basic_memory.picoschema.parser import (
    SchemaDefinition,
    parse_picoschema,
    parse_schema_note,
    parse_validation_mode,
)
from typing import Any, Literal


# Type alias for the search function dependency.
# Given a query string, returns a list of frontmatter dicts from matching schema notes.
type SchemaSearchFn = Callable[[str], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True, slots=True)
class SchemaCandidate[Source]:
    """A schema's authored definition paired with its caller-owned source.

    Keeping these together lets resolution return the source it actually used,
    without making the schema engine depend on database entities or identities.
    """

    frontmatter: dict[str, Any]
    source: Source


@dataclass(frozen=True, slots=True)
class ResolvedSchema[Source]:
    definition: SchemaDefinition
    source: Source
    kind: Literal["inline", "named"]


async def resolve_schema(
    note_frontmatter: dict[str, Any],
    search_fn: SchemaSearchFn,
) -> SchemaDefinition | None:
    """Resolve the schema for a note based on its frontmatter.

    Resolution order:
    1. Inline schema (frontmatter['schema'] is a dict) - parsed directly
    2. Explicit reference (frontmatter['schema'] is a string) - looked up via search_fn
    3. Implicit by type (frontmatter['type']) - searches for schema note with matching entity
    4. No schema - returns None

    Args:
        note_frontmatter: The YAML frontmatter dict from the note being validated.
        search_fn: An async callable that takes a query string and returns a list
            of frontmatter dicts from matching schema notes. This keeps the resolver
            decoupled from the repository/service layer.

    Returns:
        A SchemaDefinition if a schema is found, None otherwise.
    """

    async def search_candidates(query: str) -> list[SchemaCandidate[None]]:
        return [SchemaCandidate(metadata, None) for metadata in await search_fn(query)]

    resolved = await resolve_schema_with_source(
        note_frontmatter, search_candidates, inline_source=None
    )
    return resolved.definition if resolved is not None else None


async def resolve_schema_with_source[Source](
    note_frontmatter: dict[str, Any],
    search_fn: Callable[[str], Awaitable[list[SchemaCandidate[Source]]]],
    *,
    inline_source: Source,
) -> ResolvedSchema[Source] | None:
    """Resolve once, preserving the source of the definition selected.

    Callers supply their own source value, including the owner of an inline
    schema. Named lookups retain first-match selection, and a missing explicit
    reference still falls through to the note type. The definition-only API
    delegates here so both entrypoints follow the same resolution rules.
    """
    schema_value = note_frontmatter.get("schema")

    # --- 1. Inline schema ---
    # Trigger: schema field is a dict (the Picoschema definition lives in this note)
    # Why: inline schemas are self-contained, no lookup needed
    # Outcome: parse and return immediately
    if isinstance(schema_value, dict):
        return ResolvedSchema(
            _schema_from_inline(schema_value, note_frontmatter), inline_source, kind="inline"
        )

    # --- 2. Explicit reference ---
    # Trigger: schema field is a string (entity name or permalink)
    # Why: the note points to a specific schema note by name
    # Outcome: search for the referenced schema note and parse it
    if isinstance(schema_value, str):
        candidates = await search_fn(schema_value)
        if candidates:
            selected = candidates[0]
            return ResolvedSchema(
                parse_schema_note(selected.frontmatter), selected.source, kind="named"
            )

    # --- 3. Implicit by type ---
    # Trigger: no schema field, but the note has a type field
    # Why: convention — a note with type: Person looks for a schema with entity: Person
    # Outcome: search for a schema note whose entity matches the note's type
    note_type = note_frontmatter.get("type")
    if note_type:
        candidates = await search_fn(note_type)
        if candidates:
            selected = candidates[0]
            return ResolvedSchema(
                parse_schema_note(selected.frontmatter), selected.source, kind="named"
            )

    # --- 4. No schema ---
    return None


def _schema_from_inline(
    schema_dict: dict[str, Any], frontmatter: dict[str, Any]
) -> SchemaDefinition:
    """Build a SchemaDefinition from an inline schema dict.

    For inline schemas, we derive metadata from the note's own frontmatter
    since there's no separate schema note.
    """
    fields = parse_picoschema(schema_dict)
    entity = frontmatter.get("type", "unknown")
    settings = frontmatter.get("settings", {})
    validation_value = settings.get("validation", "warn") if isinstance(settings, dict) else "warn"
    validation_mode = parse_validation_mode(validation_value)

    return SchemaDefinition(
        entity=entity,
        version=1,
        fields=fields,
        validation_mode=validation_mode,
    )
