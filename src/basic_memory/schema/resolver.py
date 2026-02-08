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

from basic_memory.schema.parser import SchemaDefinition, parse_picoschema, parse_schema_note


# Type alias for the search function dependency.
# Given a query string, returns a list of frontmatter dicts from matching schema notes.
type SchemaSearchFn = Callable[[str], Awaitable[list[dict]]]


async def resolve_schema(
    note_frontmatter: dict,
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
    schema_value = note_frontmatter.get("schema")

    # --- 1. Inline schema ---
    # Trigger: schema field is a dict (the Picoschema definition lives in this note)
    # Why: inline schemas are self-contained, no lookup needed
    # Outcome: parse and return immediately
    if isinstance(schema_value, dict):
        return _schema_from_inline(schema_value, note_frontmatter)

    # --- 2. Explicit reference ---
    # Trigger: schema field is a string (entity name or permalink)
    # Why: the note points to a specific schema note by name
    # Outcome: search for the referenced schema note and parse it
    if isinstance(schema_value, str):
        result = await _schema_from_reference(schema_value, search_fn)
        if result is not None:
            return result

    # --- 3. Implicit by type ---
    # Trigger: no schema field, but the note has a type field
    # Why: convention — a note with type: Person looks for a schema with entity: Person
    # Outcome: search for a schema note whose entity matches the note's type
    note_type = note_frontmatter.get("type")
    if note_type:
        result = await _schema_from_type(note_type, search_fn)
        if result is not None:
            return result

    # --- 4. No schema ---
    return None


def _schema_from_inline(schema_dict: dict, frontmatter: dict) -> SchemaDefinition:
    """Build a SchemaDefinition from an inline schema dict.

    For inline schemas, we derive metadata from the note's own frontmatter
    since there's no separate schema note.
    """
    fields = parse_picoschema(schema_dict)
    entity = frontmatter.get("type", "unknown")
    settings = frontmatter.get("settings", {})
    validation_mode = settings.get("validation", "warn") if isinstance(settings, dict) else "warn"

    return SchemaDefinition(
        entity=entity,
        version=1,
        fields=fields,
        validation_mode=validation_mode,
    )


async def _schema_from_reference(
    ref: str,
    search_fn: SchemaSearchFn,
) -> SchemaDefinition | None:
    """Look up a schema by entity name or permalink reference.

    The search function is expected to find schema notes matching the reference.
    """
    results = await search_fn(ref)
    if not results:
        return None
    return parse_schema_note(results[0])


async def _schema_from_type(
    note_type: str,
    search_fn: SchemaSearchFn,
) -> SchemaDefinition | None:
    """Look up a schema implicitly by matching the note's type to a schema's entity field."""
    results = await search_fn(note_type)
    if not results:
        return None
    return parse_schema_note(results[0])
