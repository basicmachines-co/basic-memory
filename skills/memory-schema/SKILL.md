---
name: memory-schema
description: "Manage Basic Memory schemas: find unschemaed notes, infer and edit schemas, validate notes, detect drift. Use when structured note types (Task, Person, Meeting) need consistency across the graph."
---

# Memory Schema

Manage structured note types using Basic Memory's Picoschema system. Schemas define what fields a note type should have, making notes uniform, queryable, and validatable.

## Project scope

If the project is unknown, discover it with `bm_project(command="list")`.
Pass a nonempty `project` on every note, folder, context and schema call, even
with one project or a qualified `memory://` URL. Use the chosen project's name,
`workspace/project`, or external ID; `<project>` below is a placeholder for it.
Recent activity also requires `project`. Search accepts `project` for one
project, `projects` for selected projects, or `all_projects=True` explicitly.
Discovery and bundled manuals need no project. Keep the same scope when following
returned paths, paginating, or recovering from a refusal.

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
  status(enum, current state): [active, blocked, done, abandoned]
```

Optional enum:

```yaml
schema:
  priority?(enum, task priority): [low, medium, high, critical]
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
  validation: warn    # warn (log issues) or strict (errors)
```

Use `strict` as the canonical enforcing mode. `error` is accepted only as a compatibility alias.

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
  status?(enum, meeting state): [scheduled, completed, cancelled]
settings:
  validation: warn
---
```

## Discovering Unschemaed Notes

Look for clusters of notes that share structure but have no schema:

1. **Search by type**: `bm_search(note_types=["meeting"], project="<project>")` — if many notes share a `type` but no `schema/Meeting.md` exists, it's a candidate.

2. **Infer a schema**: Use `bm_schema(command="infer", project="<project>")` to analyze existing notes and generate a suggested schema:
   ```python
   bm_schema(command="infer", note_type="Meeting", project="<project>")
   bm_schema(command="infer", note_type="Meeting", threshold=0.5, project="<project>")  # fields in 50%+ of notes
   ```
   The threshold (0.0–1.0) controls how common a field must be to be included. Default is usually fine; lower it to catch rarer fields.

3. **Review the suggestion** — the inferred schema shows field names, types, and frequency. Decide which fields to keep, make optional, or drop.

## Creating a Schema

Write the schema note to `schema/<EntityName>`:

```python
bm_write(
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
- [convention] Action items should become tasks when complex""",
  project="<project>",
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
bm_schema(command="validate", note_type="Meeting", project="<project>")

# Validate a single note
bm_schema(command="validate", identifier="meetings/2026-02-10-standup", project="<project>")
```

**Important:** `bm_schema(command="validate", project="<project>")` checks for schema fields as **observation categories** in the note body — e.g., a `status` field expects `- [status] active` as an observation. Fields stored only in frontmatter metadata won't satisfy validation. To pass cleanly, include schema fields as both frontmatter values (for metadata search) and observations (for schema validation).

Validation reports **missing required fields** (as observation categories, or relations
for entity-reference fields) and **invalid enum values**. Undeclared observation categories
and relations are listed as informational "unmatched" items. Scalar values are not
type-checked.

### Handling Validation Results

- **`warn` mode**: Review warnings periodically. Fix notes that are clearly wrong; add optional fields to the schema for legitimate new patterns.
- **`strict` mode**: Use where conformance matters (e.g., automated pipelines consuming notes).

## Detecting Drift

Over time, notes evolve and schemas lag behind. Use `bm_schema(command="diff", project="<project>")` to find divergence:

```python
bm_schema(command="diff", note_type="Meeting", project="<project>")
```

Diff reports:
- **Fields in notes but not in schema** — candidates for adding to the schema (as optional)
- **Schema fields rarely used** — consider making optional or removing
- **Cardinality changes** — a field that moved between single-value and array

## Schema Evolution

When note structure changes:

1. **Run diff** to see current state: `bm_schema(command="diff", note_type="Meeting", project="<project>")`
2. **Update the schema note** via `bm_edit` with the `metadata` parameter. Top-level
   keys are replaced whole, so pass the complete updated `schema` map along with the new
   `version`:
   ```python
   bm_edit(
     identifier="schema/Meeting",
     command="set_metadata",
     metadata={
       "version": 2,
       "schema": {
         "topic": "string, what was discussed",
         "date": "string, when it happened",
         "attendees?(array)": "Person, who attended",
         "decisions?(array)": "string, decisions made",
         "location?": "string, where it happened"
       }
     },
     project="<project>",
   )
   ```
3. **Re-validate** to confirm existing notes still pass: `bm_schema(command="validate", note_type="Meeting", project="<project>")`
4. **Fix outliers** — update notes that don't conform to the new schema

### Evolution Guidelines

- **Additive changes** (new optional fields) are safe — no version bump needed
- **Breaking changes** (new required fields, removed fields, type changes) should bump `version`
- **Prefer optional over required** — most fields should be optional to start
- **Don't over-constrain** — schemas should describe common structure, not enforce rigid templates
- **Schema as documentation** — even if validation is set to `warn`, the schema serves as living documentation for what notes of that type should contain

## Workflow Summary

```
1. Notice repeated note structure → infer schema (bm_schema(command="infer", project="<project>"))
2. Review + create schema note   → write to schema/ (bm_write)
3. Validate existing notes       → check conformance (bm_schema(command="validate", project="<project>"))
4. Fix outliers                  → edit non-conforming notes (bm_edit)
5. Periodically check drift      → detect divergence (bm_schema(command="diff", project="<project>"))
6. Evolve schema as needed       → update schema note (bm_edit)
```
