---
name: memory-schema
description: "Schema lifecycle management for Basic Memory: discover unschemaed notes, infer schemas, create and edit schema definitions, validate notes, and detect drift. Use when working with structured note types (Task, Person, Meeting, etc.) to maintain consistency across the knowledge graph."
---

# Memory Schema

Manage structured note types using Basic Memory's Picoschema system. Schemas define what fields a note type should have, making notes uniform, queryable, and validatable.

## When to Use

- **New note type emerging** — you notice several notes share the same structure (meetings, people, decisions)
- **Validation check** — confirm existing notes conform to their schema
- **Schema drift** — detect fields that notes use but the schema doesn't define (or vice versa)
- **Schema evolution** — add/remove/change fields as requirements evolve
- **On demand** — user asks to create, check, or manage schemas

## Picoschema Syntax Reference

Schemas are defined in YAML frontmatter using Picoschema — a compact notation for describing note structure.

### Basic Types

```yaml
schema:
  name: string, person's full name
  age: integer, age in years
  score: number, floating-point rating
  active: boolean, whether currently active
```

Supported types: `string`, `integer`, `number`, `boolean`.

### Optional Fields

Append `?` to the field name:

```yaml
schema:
  title: string, required field
  subtitle?: string, optional field
```

### Enums

Use `(enum)` with a list of allowed values:

```yaml
schema:
  status(enum): [active, blocked, done, abandoned], current state
```

Optional enum:

```yaml
schema:
  priority?(enum): [low, medium, high, critical], task priority
```

### Arrays

Use `(array)` for list fields:

```yaml
schema:
  tags(array): string, categorization labels
  steps?(array): string, ordered steps to complete
```

### Relations

Reference other entity types directly:

```yaml
schema:
  parent_task?: Task, parent task if this is a subtask
  attendees?(array): Person, people who attended
```

Relations create edges in the knowledge graph, linking notes together.

### Validation Settings

```yaml
settings:
  validation: warn    # warn (log issues) or error (strict)
```

### Complete Example

```yaml
---
title: Meeting
type: schema
entity: Meeting
version: 1
schema:
  topic: string, what was discussed
  date: string, when it happened (YYYY-MM-DD)
  attendees?(array): Person, who attended
  decisions?(array): string, decisions made
  action_items?(array): string, follow-up tasks
  status?(enum): [scheduled, completed, cancelled], meeting state
settings:
  validation: warn
---
```

## Discovering Unschemaed Notes

Look for clusters of notes that share structure but have no schema:

1. **Search by type**: `search_notes(query="type:Meeting")` — if many notes share a `type` but no `schema/Meeting.md` exists, it's a candidate.

2. **Infer a schema**: Use `schema_infer` to analyze existing notes and generate a suggested schema:
   ```python
   schema_infer(noteType="Meeting")
   schema_infer(noteType="Meeting", threshold=0.5)  # fields in 50%+ of notes
   ```
   The threshold (0.0–1.0) controls how common a field must be to be included. Default is usually fine; lower it to catch rarer fields.

3. **Review the suggestion** — the inferred schema shows field names, types, and frequency. Decide which fields to keep, make optional, or drop.

## Creating a Schema

Write the schema note to `schema/<EntityName>`:

```python
write_note(
  title="Meeting",
  directory="schema",
  note_type="schema",
  metadata={
    "entity": "Meeting",
    "version": 1,
    "schema": {
      "topic": "string, what was discussed",
      "date": "string, when it happened",
      "attendees?(array)": "Person, who attended",
      "decisions?(array)": "string, decisions made"
    },
    "settings": {"validation": "warn"}
  },
  content="""# Meeting

Schema for meeting notes.

## Observations
- [convention] Meeting notes live in memory/meetings/ or as daily entries
- [convention] Always include date and topic
- [convention] Action items should become tasks when complex"""
)
```

### Key Principles

- **Schema notes live in `schema/`** — one note per entity type
- **`note_type="schema"`** marks it as a schema definition
- **`entity: Meeting`** in metadata names the type it applies to
- **`version: 1`** in metadata — increment when making breaking changes
- **`settings.validation: warn`** is recommended to start — it logs issues without blocking writes

## Validating Notes

Check how well existing notes conform to their schema:

```python
# Validate all notes of a type
schema_validate(noteType="Meeting")

# Validate a single note
schema_validate(identifier="meetings/2026-02-10-standup")
```

**Important:** `schema_validate` checks for schema fields as **observation categories** in the note body — e.g., a `status` field expects `- [status] active` as an observation. Fields stored only in frontmatter metadata won't satisfy validation. To pass cleanly, include schema fields as both frontmatter values (for metadata search) and observations (for schema validation).

Validation reports:
- **Missing required fields** — the note lacks a field the schema requires (as an observation category)
- **Unknown fields** — the note has fields the schema doesn't define
- **Type mismatches** — a field value doesn't match the expected type
- **Invalid enum values** — a value isn't in the allowed set

### Handling Validation Results

- **`warn` mode**: Review warnings periodically. Fix notes that are clearly wrong; add optional fields to the schema for legitimate new patterns.
- **`error` mode**: Use for strict schemas where conformance matters (e.g., automated pipelines consuming notes).

## Detecting Drift

Over time, notes evolve and schemas lag behind. Use `schema_diff` to find divergence:

```python
schema_diff(noteType="Meeting")
```

Diff reports:
- **Fields in notes but not in schema** — candidates for adding to the schema (as optional)
- **Schema fields rarely used** — consider making optional or removing
- **Type inconsistencies** — fields used as different types across notes

## Schema Evolution

When note structure changes:

1. **Run diff** to see current state: `schema_diff(noteType="Meeting")`
2. **Update the schema note** via `edit_note`:
   ```python
   edit_note(
     identifier="schema/Meeting",
     operation="find_replace",
     find_text="version: 1",
     content="version: 2",
     expected_replacements=1
   )
   ```
3. **Add/remove/modify fields** in the `schema:` block
4. **Re-validate** to confirm existing notes still pass: `schema_validate(noteType="Meeting")`
5. **Fix outliers** — update notes that don't conform to the new schema

### Evolution Guidelines

- **Additive changes** (new optional fields) are safe — no version bump needed
- **Breaking changes** (new required fields, removed fields, type changes) should bump `version`
- **Prefer optional over required** — most fields should be optional to start
- **Don't over-constrain** — schemas should describe common structure, not enforce rigid templates
- **Schema as documentation** — even if validation is set to `warn`, the schema serves as living documentation for what notes of that type should contain

## Workflow Summary

```
1. Notice repeated note structure → infer schema (schema_infer)
2. Review + create schema note   → write to schema/ (write_note)
3. Validate existing notes       → check conformance (schema_validate)
4. Fix outliers                  → edit non-conforming notes (edit_note)
5. Periodically check drift      → detect divergence (schema_diff)
6. Evolve schema as needed       → update schema note (edit_note)
```
