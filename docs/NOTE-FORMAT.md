# Note Format Reference

Every document in Basic Memory is a plain Markdown file. Files are the source of truth — changes to files automatically update the knowledge graph in the database. You maintain complete ownership, files work with git, and knowledge persists independently of any AI conversation.

## Document Structure

A note has three parts: YAML frontmatter, content (observations), and relations.

```markdown
---
title: Coffee Brewing Methods
type: note
tags: [coffee, brewing]
permalink: coffee-brewing-methods
---

# Coffee Brewing Methods

## Observations
- [method] Pour over provides more flavor clarity than French press
- [technique] Water temperature at 205°F extracts optimal compounds #brewing
- [preference] Ethiopian beans work well with lighter roasts (personal experience)

## Relations
- relates_to [[Coffee Bean Origins]]
- requires [[Proper Grinding Technique]]
- contrasts_with [[Tea Brewing Methods]]
```

The `## Observations` and `## Relations` headings are conventional but not required — the parser detects observations and relations by their syntax patterns anywhere in the document.

## Frontmatter

YAML metadata between `---` fences at the top of the file.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `title` | No | filename stem | Used for linking and references. Auto-set from filename if missing. |
| `type` | No | `note` | Entity type. Used for schema resolution and filtering. |
| `tags` | No | `[]` | List or comma-separated string. Used for organization and search. |
| `permalink` | No | generated from title | Stable identifier. Persists even if the file moves. |
| `schema` | No | none | Schema attachment — dict (inline), string (reference), or omitted (implicit). |

Custom fields are allowed. Any key not in the standard set is stored as `entity_metadata` and indexed for search and filtering.

```yaml
---
title: Paul Graham
type: Person
tags: [startups, essays, lisp]
permalink: paul-graham
status: active
source: wikipedia
---
```

Here `status` and `source` are custom fields stored in `entity_metadata`.

### Frontmatter Value Handling

YAML automatically converts some values to native types. Basic Memory normalizes them:

- Date strings (`2025-10-24`) → kept as ISO format strings
- Numbers (`1.0`) → converted to strings
- Booleans (`true`) → converted to strings (`"True"`)
- Lists and dicts → preserved, items normalized recursively

This prevents errors when downstream code expects string values.

## Observations

An observation is a categorized fact about the entity. Written as a Markdown list item.

**Syntax:**

```
- [category] content text #tag1 #tag2 (context)
```

| Part | Required | Description |
|------|----------|-------------|
| `[category]` | Yes | Classification in square brackets. Any text except `[]()` chars. |
| content | Yes | The fact or statement. |
| `#tags` | No | Inline tags. Space-separated, each starting with `#`. |
| `(context)` | No | Parenthesized text at end of line. Supporting details or source. |

### Examples

```markdown
- [tech] Uses SQLite for storage #database
- [design] Follows local-first architecture #architecture
- [decision] Selected bcrypt for passwords #security (based on OWASP audit)
- [name] Paul Graham
- [expertise] Startups
- [expertise] Lisp
- [expertise] Essay writing
```

Array-like fields use repeated categories — multiple `[expertise]` observations above.

### What Is Not an Observation

The parser excludes these list item patterns:

| Pattern | Example | Reason |
|---------|---------|--------|
| Checkboxes | `- [ ] Todo item`, `- [x] Done`, `- [-] Cancelled` | Task list syntax |
| Markdown links | `- [text](url)` | URL link syntax |
| Bare wiki links | `- [[Target]]` | Treated as a relation instead |

A list item with `#tags` but no `[category]` is still parsed — the tags are extracted and the category defaults to `Note`.

## Relations

Relations connect documents to form the knowledge graph. There are two kinds.

### Explicit Relations

Written as list items with a relation type and a `[[wiki link]]` target.

**Syntax:**

```
- relation_type [[Target Entity]] (context)
```

| Part | Required | Description |
|------|----------|-------------|
| `relation_type` | No | Text before `[[`. Defaults to `relates_to` if omitted. |
| `[[Target]]` | Yes | Wiki link to the target entity. Matched by title or permalink. |
| `(context)` | No | Parenthesized text after `]]`. Supporting details. |

### Examples

```markdown
- implements [[Search Design]]
- depends_on [[Database Schema]]
- works_at [[Y Combinator]] (co-founder)
- [[Some Entity]]
```

The last example — a bare `[[wiki link]]` in a list item — gets relation type `relates_to`.

Common relation types:
- `implements`, `depends_on`, `relates_to`, `inspired_by`
- `extends`, `part_of`, `contains`, `pairs_with`
- `works_at`, `authored`, `collaborated_with`

Any text works as a relation type. These are conventions, not a fixed set.

### Inline References

Wiki links appearing in regular prose (not as list items) create implicit `links_to` relations.

```markdown
This builds on [[Core Design]] and uses [[Utility Functions]].
```

This creates two relations: `links_to [[Core Design]]` and `links_to [[Utility Functions]]`.

### Forward References

Relations can link to entities that don't exist yet. Basic Memory resolves them when the target is created.

## Permalinks and memory:// URLs

Every document has a unique **permalink** — a stable identifier derived from its title. You can set one explicitly in frontmatter, or let the system generate it.

```yaml
permalink: auth-approaches-2024
```

Permalinks form the basis of `memory://` URLs:

```
memory://auth-approaches-2024        # By permalink
memory://Authentication Approaches   # By title (auto-resolves)
memory://project/auth-approaches     # By path
```

Pattern matching is supported:

```
memory://auth*                       # Starts with "auth"
memory://*/approaches                # Ends with "approaches"
memory://project/*/requirements      # Nested wildcard
```

## Schemas

Schemas declare the expected structure of a note — which observation categories and relation types a well-formed note should have. They use Picoschema, a compact notation from Google's Dotprompt that fits naturally in YAML frontmatter.

### Picoschema Syntax

```yaml
schema:
  name: string, full name              # required field with description
  email?: string, contact email        # ? = optional
  role?: string, job title
  works_at?: Organization, employer    # capitalized type = entity reference
  tags?(array): string, categories     # array of type
  status?(enum): [active, inactive]    # enum with allowed values
  metadata?(object):                   # nested object
    updated_at?: string
    source?: string
```

| Notation | Meaning | Example |
|----------|---------|---------|
| `field: type` | Required field | `name: string` |
| `field?: type` | Optional field | `role?: string` |
| `field(array): type` | Array of values | `expertise(array): string` |
| `field?(enum): [vals]` | Enum with allowed values | `status?(enum): [active, inactive]` |
| `field?(object):` | Nested object with sub-fields | `metadata?(object):` |
| `, description` | Description after comma | `name: string, full name` |
| `EntityName` | Capitalized type = entity reference | `works_at?: Organization` |

**Scalar types:** `string`, `integer`, `number`, `boolean`, `any`

Any type not in that set whose first letter is uppercase is treated as an entity reference (a relation target).

### Schema-to-Note Mapping

Schemas validate against existing observation/relation syntax. Note authors don't learn new syntax.

| Schema Declaration | Maps To | Example in Note |
|--------------------|---------|-----------------|
| `field: string` | Observation `[field] value` | `- [name] Paul Graham` |
| `field?(array): string` | Multiple `[field]` observations | `- [expertise] Lisp` (repeated) |
| `field?: EntityType` | Relation `field [[Target]]` | `- works_at [[Y Combinator]]` |
| `field?(array): EntityType` | Multiple `field` relations | `- authored [[Book]]` (repeated) |
| `tags` | Frontmatter `tags` array | `tags: [startups, essays]` |
| `field?(enum): [vals]` | Observation `[field] value` where value is in the set | `- [status] active` |

Observations and relations not covered by the schema are valid — schemas describe a subset, not a straitjacket.

### Schema Attachment

Three ways to attach a schema to a note, resolved in priority order:

**1. Inline schema** — `schema` is a dict in frontmatter:

```yaml
---
title: Team Standup 2024-01-15
type: meeting
schema:
  attendees(array): string, who was there
  decisions(array): string, what was decided
  action_items(array): string, follow-ups
  blockers?(array): string, anything stuck
---
```

Good for one-off structured notes or prototyping a schema before extracting it.

**2. Explicit reference** — `schema` is a string naming a schema note:

```yaml
---
title: Basic Memory
schema: SoftwareProject
---
```

or by permalink:

```yaml
---
title: LLM Memory Patterns
schema: schema/research-project
---
```

Use when the note's `type` differs from the schema it should validate against, or when multiple schema variants exist.

**3. Implicit by type** — no `schema` field, resolved by matching `type`:

```yaml
---
title: Paul Graham
type: Person
---
```

The system looks up a schema note where `entity: Person`. If found, it applies. If not, no validation occurs.

**4. No schema** — perfectly fine. Most notes don't need one.

### Schema Notes

A schema is itself a Basic Memory note with `type: schema`. It lives anywhere (though `schema/` is the conventional directory).

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

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Must be `schema` |
| `entity` | Yes | The entity type this schema describes (e.g., `Person`) |
| `version` | No | Schema version number (default: `1`) |
| `schema` | Yes | Picoschema dict defining the fields |
| `settings.validation` | No | Validation mode (default: `warn`) |

Schema notes are regular notes — they show up in search, can have observations and relations, and participate in the knowledge graph.

### Validation Modes

| Mode | Behavior |
|------|----------|
| `warn` | Warnings in output, doesn't block (default) |
| `strict` | Errors that block sync, for CI/CD enforcement |
| `off` | No validation |

### Validation Output

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

### Schema Inference

Generate schemas from existing notes by analyzing observation and relation frequency:

```
$ bm schema infer Person

Analyzing 30 notes with type: Person...

Observations found:
  [name]        30/30  100%  → name: string
  [role]        27/30   90%  → role?: string
  [expertise]   18/30   60%  → expertise?(array): string
  [email]        8/30   27%  → email?: string

Relations found:
  works_at      22/30   73%  → works_at?: Organization

Suggested schema:
  name: string, full name
  role?: string, job title
  expertise?(array): string, areas of knowledge
  email?: string, contact email
  works_at?: Organization, employer

Save to schema/Person.md? [y/n]
```

Frequency thresholds:
- **100% present** → required field
- **25%+ present** → optional field
- **Below 25%** → excluded from suggestion

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

## Complete Examples

### Simple Note (No Schema)

```markdown
---
title: Project Ideas
type: note
tags: [ideas, brainstorm]
---

# Project Ideas

## Observations
- [idea] Build a CLI tool for markdown linting #tooling
- [idea] Create a recipe knowledge base #cooking
- [priority] Focus on developer tools first (Q1 goal)

## Relations
- inspired_by [[Developer Workflow Research]]
- part_of [[Q1 Planning]]
```

### Schema-Validated Note

Schema at `schema/Person.md`:

```yaml
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

Note at `people/paul-graham.md`:

```markdown
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

The `[fact]` observation and `authored` relation are not in the schema — they're valid, just unmatched. The schema only checks that `[name]` exists (required) and looks for optional fields like `[role]`, `[expertise]`, and `works_at`.

### Inline Schema Note

```markdown
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
