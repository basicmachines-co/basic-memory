---
name: memory-notes
description: "How to write well-structured Basic Memory notes: frontmatter, observations with semantic categories, relations with wiki-links, and best practices for building a rich knowledge graph. Use when creating or improving notes."
---

# Memory Notes

Write well-structured notes that Basic Memory can parse into a searchable knowledge graph. Every note is a markdown file with three key sections: frontmatter, observations, and relations.

## Note Anatomy

```markdown
---
title: API Design Decisions
tags: [api, architecture, decisions]
---

# API Design Decisions

The API team evaluated multiple approaches for the public API during Q1. After
prototyping both REST and GraphQL, the team chose REST due to broader ecosystem
support and simpler caching semantics. This note captures the key decisions and
their rationale, along with open questions still to resolve.

## Observations
- [decision] Use REST over GraphQL for simplicity #api
- [requirement] Must support versioning from day one
- [risk] Rate limiting needed for public endpoints

## Relations
- implements [[API Specification]]
- depends_on [[Authentication System]]
- relates_to [[Performance Requirements]]
```

### Frontmatter

Every note starts with YAML frontmatter:

```yaml
---
title: Note Title          # required — becomes the entity name in the knowledge graph
tags: [tag1, tag2]         # optional — for organization and filtering
type: note                 # optional — defaults to "note", use custom types with schemas
permalink: custom-path     # optional — auto-generated from title if omitted
---
```

- The `title` must match the `# Heading` in the body
- Tags are searchable and help with discovery
- Custom `type` values (Task, Meeting, Person, etc.) work with the schema system. See the **memory-schema** skill for defining schemas, validating notes against them, and detecting drift.
- The `permalink` is auto-generated from the `title` and `directory`. For example, title "API Design Decisions" in directory "specs" produces permalink `specs/api-design-decisions` and memory URL `memory://specs/api-design-decisions`. If no directory is specified, the permalink is just the kebab-cased title. Permalinks stay stable across file moves. You rarely need to set one manually.

> **Note:** When using `write_note`, you don't write frontmatter yourself. The `title`, `tags`, `note_type`, and `metadata` are separate parameters — Basic Memory generates the frontmatter automatically. Your `content` parameter is just the markdown body starting with `# Heading`.

### Body / Context

Free-form markdown between the heading and the Observations section. This is the heart of the note — write generously here:
- Background, motivation, and history
- Detailed explanation of what happened and why it matters
- Analysis, reasoning, and trade-offs considered
- Context that someone (or an AI) needs to understand this note later

Write complete, substantive prose. Basic Memory's search retrieves relevant chunks from note bodies, so longer, richer context makes notes more discoverable and more useful when found. Don't reduce everything to bullet points — tell the story.

## Observations

Observations are categorized facts — the atomic units of knowledge. Each one becomes a searchable entity in the knowledge graph.

### Syntax

```
- [category] Content of the observation #optional-tag
```

- **Square brackets** define the semantic category
- **Content** is the fact, decision, insight, or note
- **Hash tags** (optional) add extra metadata for filtering

### Categories Are Arbitrary

The category in brackets is free-form — use whatever label makes sense for the observation. There is no fixed list. The only rule is the `[category] content` syntax. Consistency within a project helps searchability, but invent categories freely.

A few examples to illustrate the range:

```
- [decision] Use PostgreSQL for primary data store
- [risk] Third-party API has no SLA guarantee
- [technique] Exponential backoff for retry logic #resilience
- [question] Should we support multi-tenancy at the DB level?
- [preference] Use Bun over Node for new projects
- [lesson] Always validate webhook signatures server-side
- [status] active
- [flavor] Ethiopian beans work best with lighter roasts
```

### Observation Tips

- **One fact per observation.** Don't pack multiple ideas into one line.
- **Be specific.** `[decision] Use JWT` is less useful than `[decision] Use JWT with 15-minute expiry for API auth`.
- **Use tags for cross-cutting concerns.** `[risk] Rate limiting needed #api #security` makes this findable under both topics.
- **Categories are queryable.** `search_notes("[decision]")` finds all decisions across your knowledge base.

## Relations

Relations create edges in the knowledge graph, linking notes to each other. They're how you build structure beyond individual notes.

### Syntax

```
- relation_type [[Target Note Title]]
```

- **relation_type** is a descriptive verb or phrase (snake_case by convention)
- **Double brackets** `[[...]]` identify the target note by title or permalink
- Relations are directional: this note → target note

### Relation Types

| Type | Purpose | Example |
|------|---------|---------|
| `implements` | One thing implements another | `- implements [[Auth Spec]]` |
| `requires` | Dependencies | `- requires [[Database Setup]]` |
| `relates_to` | General connection | `- relates_to [[Performance Notes]]` |
| `part_of` | Hierarchy/composition | `- part_of [[Backend Architecture]]` |
| `extends` | Enhancement or elaboration | `- extends [[Base Config]]` |
| `pairs_with` | Things that work together | `- pairs_with [[Frontend Client]]` |
| `inspired_by` | Source material | `- inspired_by [[CRDT Research Paper]]` |
| `replaces` | Supersedes another note | `- replaces [[Old Auth Design]]` |
| `depends_on` | Runtime/build dependency | `- depends_on [[MCP SDK]]` |
| `contrasts_with` | Alternative approaches | `- contrasts_with [[GraphQL Approach]]` |

### Inline Relations

Wiki-links anywhere in the note body — not just the Relations section — also create graph edges:

```markdown
We evaluated [[GraphQL Approach]] but decided against it because
the team has more experience with REST. See [[API Specification]]
for the full contract.
```

These create `references` relations automatically. Use the Relations section for explicit, typed relationships; use inline links for natural prose references.

### Relation Tips

- **Link liberally.** Relations are what turn isolated notes into a knowledge graph. When in doubt, add the link.
- **Create target notes if they don't exist yet.** `[[Future Topic]]` is valid — BM will resolve it when that note is created.
- **Use `build_context` to traverse.** `build_context(url="memory://note-title")` follows relations to gather connected knowledge.
- **Custom relation types are fine.** `taught_by`, `blocks`, `tested_in` — use whatever is descriptive.

## Memory URLs

Every note is addressable via a `memory://` URL, built from its permalink. These URLs are how you navigate the knowledge graph programmatically.

### URL Patterns

```
memory://api-design-decisions          # by permalink (title → kebab-case)
memory://docs/authentication           # by file path
memory://docs/authentication.md        # with extension (also works)
memory://auth*                         # wildcard prefix
memory://docs/*                        # wildcard suffix
memory://project/*/requirements        # path wildcards
```

### Project-Scoped URLs

In multi-project setups, prefix with the project name:

```
memory://main/specs/api-design         # "main" project, "specs/api-design" path
memory://research/papers/crdt          # "research" project
```

The first path segment is matched against known project names. If it matches, it's used as the project scope. Otherwise the URL resolves in the default project.

### Using Memory URLs

Memory URLs work with `build_context` to assemble related knowledge by traversing relations:

```python
# Get a note and its connected context
build_context(url="memory://api-design-decisions")

# Wildcard — gather all docs
build_context(url="memory://docs/*")

# Direct read by permalink
read_note(identifier="memory://api-design-decisions")
```

## Before Creating a Note

Always search Basic Memory before creating a new note. Duplicates fragment your knowledge graph — updating an existing note is almost always better than creating a second one.

### Search with Multiple Variations

A single search often misses. Try the full name, abbreviations, acronyms, and keywords:

```python
# Searching for an entity that might already exist
search_notes(query="Kubernetes Migration")
search_notes(query="k8s migration")
search_notes(query="container migration")
```

For people, try full name and last name. For organizations, try the full name and common abbreviations.

### Decision Tree

- **Entity exists** → Update it with `edit_note` (append observations, add relations, find-and-replace outdated info)
- **Entity doesn't exist** → Create it with `write_note`
- **Unsure if it's the same entity** → Read the existing note first, then decide

### Granular Updates with `edit_note`

When a note already exists, make targeted edits instead of rewriting the whole file:

```python
# Append a new observation to an existing note
edit_note(
  identifier="API Design Decisions",
  operation="append",
  section="Observations",
  content="- [decision] Switched to OpenAPI 3.1 for spec generation #api"
)

# Fix outdated information
edit_note(
  identifier="API Design Decisions",
  operation="find_replace",
  find_text="- [status] draft",
  content="- [status] approved"
)

# Add a new relation
edit_note(
  identifier="API Design Decisions",
  operation="append",
  section="Relations",
  content="- depends_on [[Rate Limiter]]"
)
```

This preserves existing content and keeps the edit history clean.

## Writing Notes with Tools

### Creating a Note

```python
write_note(
  title="API Design Decisions",
  directory="architecture",
  tags=["api", "architecture"],
  content="""# API Design Decisions

The API team evaluated REST and GraphQL during Q1 planning. After prototyping
both approaches, we chose REST for the public API — broader ecosystem support,
simpler caching with HTTP semantics, and a lower learning curve for external
consumers. GraphQL remains an option for internal services where query
flexibility matters more.

## Observations
- [decision] Use REST for public API #api
- [requirement] Support API versioning from v1

## Relations
- implements [[API Specification]]
- relates_to [[Backend Architecture]]"""
)
```

Basic Memory auto-generates frontmatter (including the permalink and memory URL) from the parameters. This note would get permalink `architecture/api-design-decisions` and be addressable at `memory://architecture/api-design-decisions`.

### Editing an Existing Note

Use `edit_note` to append, prepend, or find-and-replace within a note:

```python
# Append new observations
edit_note(
  identifier="API Design Decisions",
  operation="append",
  section="Observations",
  content="- [decision] Use OpenAPI 3.1 for spec generation #api"
)

# Add a new relation
edit_note(
  identifier="API Design Decisions",
  operation="append",
  section="Relations",
  content="- depends_on [[Rate Limiter]]"
)
```

### Moving a Note

Use `move_note` to reorganize notes into different directories:

```python
move_note(
  identifier="API Design Decisions",
  destination_path="archive/api-design-decisions.md"
)
```

The permalink stays the same after a move, so all `[[wiki-links]]` and `memory://` URLs continue to resolve.

## Best Practices

1. **Start with context.** Before listing observations, explain *why* this note exists. Future-you (or your AI collaborator) will thank you.

2. **Favor completeness.** Write rich, substantive notes. Basic Memory's search pulls relevant chunks from note bodies, so longer notes with more context are *more* discoverable, not less. Use prose in the body to tell the full story — the background, the reasoning, the nuance. Then distill key facts into `[category] content` observations for structured queries. Both matter: prose gives meaning, observations give precision.

3. **Build incrementally.** Add to existing notes rather than creating duplicates. Use `edit_note` to append new observations or relations as you learn more.

4. **Review AI-generated content.** When an AI writes notes for you, review them for accuracy. The AI captures structure well but may miss nuance.

5. **Use consistent titles.** Note titles are identifiers in the knowledge graph. `API Design Decisions` and `Api Design decisions` are different entities. Pick a convention and stick with it.

6. **Link related concepts.** The value of a knowledge graph compounds with connections. A note with zero relations is an island — useful, but not as powerful as a connected one.

7. **Let the graph grow naturally.** Don't try to design a perfect taxonomy upfront. Write notes as you work, add relations as connections emerge, and periodically use `/reflect` or `/defrag` to consolidate.
