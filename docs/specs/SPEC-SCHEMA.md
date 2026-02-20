# SPEC-SCHEMA: Basic Memory Schema System

**Status:** Draft
**Created:** 2025-02-06
**Branch:** `feature/schema-system`

## Summary

A schema system for Basic Memory that uses [Picoschema](https://genkit.dev/docs/dotprompt/)
syntax in YAML frontmatter. Schemas validate notes against their existing observation/relation
structure — no new data model, no migration, just a declarative lens over what's already there.

## Core Principles

1. **Schemas are just notes** — A schema is a note with `type: schema`, lives anywhere
2. **Use prior art** — Picoschema syntax in YAML frontmatter, no custom notation
3. **Validation maps to existing format** — Observations and relations, not a parallel data model
4. **Validation is soft** — Warnings by default, not blocking errors
5. **Inference over prescription** — Schemas describe reality, emerge from usage
6. **No built-in agent** — Programmatic core; the LLM already in the session provides intelligence

## Picoschema Syntax

Picoschema is a compact schema notation from Google's Dotprompt that fits naturally in YAML
frontmatter.

### Supported Types

| Type | Description |
|------|-------------|
| `string` | Text value |
| `integer` | Whole number |
| `number` | Decimal number |
| `boolean` | True/false |
| `any` | Any scalar type |
| `EntityName` | Reference to another entity (capitalized = entity reference) |

### Syntax Rules

```yaml
schema:
  name: string, full name                # required field with description
  email?: string, contact email           # ? = optional
  role?: string, job title
  works_at?: Organization, employer       # capitalized type = entity reference
  tags?(array): string, categories        # array of type
  status?(enum): [active, inactive]       # enum with allowed values
  metadata?(object):                      # nested object
    updated_at?: string
    source?: string
```

- `field: type` — required field
- `field?: type` — optional field
- `field(array): type` — array of values
- `field?(enum): [values]` — enumeration
- `field?(object):` — nested object with sub-fields
- `, description` — description after comma
- `EntityName` as type (capitalized) — reference to another entity

## Schema-to-Note Mapping

Schemas validate against the existing Basic Memory note format. No new syntax for note
authors to learn.

### Mapping Rules

| Schema Declaration | Grounded In | Example Match |
|--------------------|-------------|---------------|
| `field: string` | Observation `[field] value` | `- [name] Paul Graham` |
| `field?(array): string` | Multiple `[field]` observations | `- [expertise] Lisp` (×N) |
| `field?: EntityType` | Relation `field [[Target]]` | `- works_at [[Y Combinator]]` |
| `field?(array): EntityType` | Multiple `field` relations | `- authored [[Book]]` (×N) |
| `tags` | Frontmatter `tags` array | `tags: [startups, essays]` |
| `field?(enum): [values]` | Observation `[field] value` where value ∈ set | `- [status] active` |

### Key Insight

Schemas don't introduce a new way to store data. They describe the patterns already present
in observations and relations. A note doesn't have to change how it's written — the schema
just says "a good Person note has a `[name]` observation and a `works_at` relation."

## Schema Definition

### As a Dedicated Schema Note

```yaml
# schema/Person.md
---
title: Person
type: schema
entity: Person
version: 1
schema:
  name: string, full name
  email?: string, contact email
  role?: string, job title
  works_at?: Organization, employer
  expertise?(array): string, areas of knowledge
settings:
  validation: warn    # warn | strict | off
---

# Person

A human individual in the knowledge graph.

Any documentation about this entity type goes here as prose.
```

Schema notes are regular Basic Memory notes. They show up in search, can have their own
observations and relations, and can be organized in any folder (though `schema/` is
the suggested convention).

### Inline Schema in a Note

Notes can carry their own schema directly:

```yaml
# meetings/2024-01-15-standup.md
---
title: Team Standup 2024-01-15
type: meeting
schema:
  attendees(array): string, who was there
  decisions(array): string, what was decided
  action_items(array): string, follow-ups
  blockers?(array): string, anything stuck
---

# Team Standup 2024-01-15

## Observations
- [attendees] Paul
- [attendees] Sarah
- [decisions] Ship v2 by Friday
- [action_items] Paul to review PR #42
- [blockers] Waiting on API credentials
```

Good for one-off structured notes or prototyping a schema before extracting it.

### Explicit Schema Reference

A note can reference a schema by entity name or permalink:

```yaml
# projects/basic-memory.md
---
title: Basic Memory
schema: SoftwareProject         # by entity name
---

# research/llm-memory-patterns.md
---
title: LLM Memory Patterns
schema: schema/research-project  # by permalink
---
```

Use cases:
- Note's `type` differs from the schema it should validate against
- Multiple schema variants exist for the same domain
- Applying structure to existing notes without changing their type

## Schema Resolution

When validating a note, schemas resolve in priority order:

```
1. Inline schema     →  schema: { ... }        (dict in frontmatter)
2. Explicit ref      →  schema: Person          (string in frontmatter)
3. Implicit by type  →  type: Person            (lookup schema note with entity: Person)
4. No schema         →  no validation           (perfectly fine)
```

```python
async def resolve_schema(note: Note) -> Schema | None:
    schema_value = note.frontmatter.get('schema')

    # 1. Inline schema (dict)
    if isinstance(schema_value, dict):
        return parse_picoschema(schema_value)

    # 2. Explicit reference (string)
    if isinstance(schema_value, str):
        schema_note = await find_schema_note(schema_value)
        if schema_note:
            return parse_picoschema(schema_note.frontmatter['schema'])

    # 3. Implicit by type
    note_type = note.frontmatter.get('type')
    if note_type:
        results = await search_notes(f"type:schema entity:{note_type}")
        if results:
            return parse_picoschema(results[0].frontmatter['schema'])

    # 4. No schema
    return None
```

## Validation

### Modes

Configured in the schema's `settings.validation`:

| Mode | Behavior |
|------|----------|
| `off` | No validation |
| `warn` | Warnings in output, doesn't block (default) |
| `strict` | Errors that block sync, for CI/CD enforcement |

### Validation Output

For a note missing required fields:

```
$ bm schema validate people/ada-lovelace.md

⚠ Person schema validation:
  - Missing required field: name (expected [name] observation)
  - Missing optional field: role
  - Missing optional field: works_at (no relation found)

ℹ Unmatched observations: [fact] ×2, [born] ×1
ℹ Unmatched relations: collaborated_with
```

"Unmatched" items are informational — observations and relations the schema doesn't cover.
They're valid. Schemas are a subset, not a straitjacket.

### Batch Validation

```
$ bm schema validate Person

Validating 30 notes against Person schema...

✓ people/paul-graham.md         — all fields present
✓ people/rich-hickey.md         — all fields present
⚠ people/ada-lovelace.md        — missing: name
⚠ people/alan-kay.md            — missing: name, role
✓ people/linus-torvalds.md      — all fields present
...

Summary: 22/30 valid, 8 warnings, 0 errors
```

## Emerging Schemas

### The Problem with Traditional Schemas

Most schema systems require: define schema → create conforming content → fight the schema
when reality doesn't match. This is backwards. Knowledge grows organically.

### The Basic Memory Approach

```
Write notes freely → Patterns emerge → Crystallize into schema → Validate future notes
```

### Schema Inference

Generate schemas from existing notes by analyzing observation and relation frequency:

```
$ bm schema infer Person

Analyzing 30 notes with type: Person...

Observations found:
  [name]        30/30  100%  → name: string
  [role]        27/30   90%  → role?: string
  [fact]        25/30   83%  (generic — no single field)
  [expertise]   18/30   60%  → expertise?(array): string
  [email]        8/30   27%  → email?: string
  [born]         6/30   20%  (below threshold)

Relations found:
  works_at      22/30   73%  → works_at?: Organization
  authored      11/30   37%  → authored?(array): string

Suggested schema:
  name: string, full name
  role?: string, job title
  expertise?(array): string, areas of knowledge
  email?: string, contact email
  works_at?: Organization, employer

Save to schema/Person.md? [y/n]
```

Frequency thresholds:
- 100% present → required field
- 25%+ present → optional field
- Below 25% → excluded from suggestion (but noted)

### Schema Drift Detection

Track how usage patterns shift over time:

```
$ bm schema diff Person

Schema drift detected:

+ expertise: now in 81% of notes (was 12%)
- department: dropped to 3% of notes
~ works_at: cardinality changed (one → many)

Update schema? [y/n/review]
```

## LLM Integration (AI Guidance)

No agent runtime or API key required. The LLM already in the session uses schemas as
context for note creation.

### Flow

1. User asks LLM to "write a note about Rich Hickey"
2. LLM determines `type: Person` is appropriate
3. LLM calls `search_notes("type:schema entity:Person")` → finds schema
4. LLM reads schema fields: required `name`, optional `role`, `works_at`, `expertise`
5. LLM calls `write_note` with observations and relations that satisfy the schema

The schema acts as a creation template. The LLM knows what a "complete" note looks like
without any custom agent infrastructure.

### MCP Tools

```python
@mcp_tool
async def schema_validate(
    entity_type: str | None = None,
    identifier: str | None = None,
    project: str | None = None,
) -> ValidationReport:
    """Validate notes against their resolved schema.

    Validates a specific note (by identifier) or all notes of a given type.
    Returns warnings/errors based on the schema's validation mode.
    """

@mcp_tool
async def schema_infer(
    entity_type: str,
    threshold: float = 0.25,
    project: str | None = None,
) -> SuggestedSchema:
    """Analyze existing notes and suggest a schema definition.

    Examines observation categories and relation types across all notes
    of the given type. Returns frequency analysis and suggested Picoschema.
    """
```

## CLI Commands

```bash
# Validate a specific note
bm schema validate people/ada-lovelace.md

# Validate all notes of a type
bm schema validate Person

# Validate everything with a schema
bm schema validate

# Infer schema from existing notes
bm schema infer Person

# Show schema drift from current definition
bm schema diff Person

# List all schema notes
bm search "type:schema"
```

## Examples

### Complete Person Workflow

**Schema:**
```yaml
# schema/Person.md
---
title: Person
type: schema
entity: Person
version: 1
schema:
  name: string, full name
  role?: string, job title or position
  works_at?: Organization, employer
  expertise?(array): string, areas of knowledge
  email?: string, contact email
settings:
  validation: warn
---

# Person

A human individual in the knowledge graph.
```

**Valid note:**
```yaml
# people/paul-graham.md
---
title: Paul Graham
type: Person
tags: [startups, essays, lisp]
---

# Paul Graham

## Observations
- [name] Paul Graham
- [role] Essayist and investor
- [expertise] Startups
- [expertise] Lisp
- [expertise] Essay writing
- [fact] Created Viaweb, the first web app

## Relations
- works_at [[Y Combinator]]
- authored [[Hackers and Painters]]
```

**Note with warnings:**
```yaml
# people/ada-lovelace.md
---
title: Ada Lovelace
type: Person
---

# Ada Lovelace

## Observations
- [fact] Wrote the first computer program
- [born] 1815

## Relations
- collaborated_with [[Charles Babbage]]
```

Validation: warns about missing required `[name]` observation. Everything else is optional
or unmatched (which is fine).

## Future Considerations (Deferred)

These are interesting but out of scope for the initial implementation:

- **Multiple schema inheritance** — `schema: [Person, Author]`
- **Hook integration** — Pre-write validation via the hooks system
- **OWL/RDF export** — `bm schema export --format owl`
- **SPARQL queries** — Schema-aware graph queries
- **Built-in templates** — `bm schema use gtd`, `bm schema use zettelkasten`
- **Schema versioning/migration** — Tracking breaking changes across versions
