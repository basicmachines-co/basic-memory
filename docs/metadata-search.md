# Metadata Search Reference

Basic Memory automatically indexes custom frontmatter fields so you can query them with structured filters. Any YAML key in a note's frontmatter beyond the standard set (`title`, `type`, `tags`, `permalink`, `schema`) is stored as `entity_metadata` and becomes searchable.

## Querying with `bm_search`

`bm_search` ranks text and semantic queries, optionally narrowed by metadata. Use
`bm_find` for folder-scoped frontmatter inventories and selected fields. The `query` parameter is optional, so you can use metadata filters alone without passing an empty string.

## Filter Syntax

Filters are a JSON dictionary where each key targets a frontmatter field and the value specifies the match condition. Multiple keys combine with **AND** logic — every filter must match.

Metadata filters only ever match Markdown notes. A project can also index PDFs, images and other regular files, but those carry no frontmatter, so they are never metadata hits and never counted in the result total — including for the null filter below, which asks about a *missing* value. A regular file's entity records its MIME type and short format name (`content_type` and `format`, such as `application/pdf` and `pdf`). API responses and directory listings return them, but metadata filters do not match them.

### Equality

Match a single value exactly.

```json
{"status": "active"}
```

Finds notes whose frontmatter contains `status: active`.

### Array Contains (all)

Pass a list to require **all** listed values to be present in the field.

```json
{"tags": ["security", "oauth"]}
```

Finds notes tagged with both `security` and `oauth`.

### `$in` (any of)

Match if the field equals **any** value in the list.

```json
{"priority": {"$in": ["high", "critical"]}}
```

### `$gt`, `$gte`, `$lt`, `$lte`

Numeric and text comparisons. Numeric values use numeric comparison; strings use lexicographic comparison.

```json
{"confidence": {"$gt": 0.7}}
{"score": {"$lte": 100}}
```

### `$between`

Range filter (inclusive). Takes a `[min, max]` pair.

```json
{"score": {"$between": [0.3, 0.8]}}
```

### Null (missing or explicitly null)

A `None` value asks whether a note carries any value for the field at all.

```json
{"owner": null}
```

Matches notes whose frontmatter has no `owner` key, and notes whose `owner` is
explicitly null. Both backends extract those two cases to SQL `NULL`, so the
filter answers the same note set on SQLite and Postgres.

Null works only through equality. Inside `$in`, `$between`, an array-contains
list, or a comparison it is rejected: those compile to a SQL comparison against
the value, and a comparison with `NULL` is never true, so the filter would
report a confident zero rather than the query it cannot express.

### Nested Access (dot notation)

Access nested frontmatter values using dots.

```json
{"schema.version": "2"}
```

This queries the `version` key inside a `schema` object in frontmatter.

### Summary Table

| Operator | Syntax | Example |
|----------|--------|---------|
| Equality | `{"field": "value"}` | `{"status": "active"}` |
| Is null | `{"field": null}` | `{"owner": null}` |
| Array contains (all) | `{"field": ["a", "b"]}` | `{"tags": ["security", "oauth"]}` |
| `$in` (any of) | `{"field": {"$in": [...]}}` | `{"priority": {"$in": ["high", "critical"]}}` |
| `$gt` / `$gte` | `{"field": {"$gt": N}}` | `{"confidence": {"$gt": 0.7}}` |
| `$lt` / `$lte` | `{"field": {"$lt": N}}` | `{"score": {"$lt": 0.5}}` |
| `$between` | `{"field": {"$between": [min, max]}}` | `{"score": {"$between": [0.3, 0.8]}}` |
| Nested access | `{"a.b": "value"}` | `{"schema.version": "2"}` |

**Key rules:**
- Filter keys must match `[A-Za-z0-9_-]+` (dots separate nesting levels).
- Several operators on one field combine with AND logic.
- `$in` and array-contains require non-empty lists.
- `$between` requires exactly two values `[min, max]`.
- `null` is an is-null match and only valid as a plain equality value.
- Comparison and `$between` bounds must be finite numbers. A magnitude no float can hold — a 400-digit integer, which JSON keeps as an ordinary `int` — is refused as a filter error rather than compared against an infinite bound.

## MCP Tool — `bm_search`

`bm_search` is the single search tool for text queries, metadata filters, or both. The `query` parameter is optional.

**Relevant parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string (optional) | Text search query. Omit for filter-only searches. |
| `filters` | dict | Structured filter dict (see syntax above) |
| `project` | string | Project name, workspace-qualified name, or external UUID |

Put `tags` and `status` inside `filters`; they are not separate MCP parameters.

**Examples:**

```python
# Text search filtered by metadata
await bm_search("authentication", filters={"status": "draft"})

# Filter-only search (no query needed)
await bm_search(filters={"type": "spec"})

# Combine text, tags, and metadata
await bm_search(
    "oauth flow",
    filters={"tags": ["security"], "confidence": {"$gt": 0.7}},
)

# Metadata filters
await bm_search("planning", filters={"status": "active"})
await bm_search(filters={"tags": ["tier1", "alpha"]})
```

## Tag Search Shortcuts

The `tag:` prefix in a search query is a shorthand for tag-based metadata filtering. When `bm_search` receives a query starting with `tag:`, it converts the query into a `tags` filter and clears the text query.

```python
# These are equivalent:
await bm_search("tag:tier1")
await bm_search(filters={"tags": ["tier1"]})

# Multiple tags (comma or space separated) — all must be present:
await bm_search("tag:tier1,alpha")
await bm_search("tag:tier1 alpha")
```

For a scoped inventory with selected metadata fields:

```python
await bm_find(path="specs", filters={"status": "draft"},
              fields=["title", "priority"], project="main")
```

`bm_find` cannot combine `filters` with `name` or a non-default `depth`.
Both tools paginate; follow `has_more` and use at most 100 results per page.
Examples without `project` use the configured default; pass an explicit route
when working across projects.

## CLI Access

The `bm tool search` command exposes metadata filtering via `--meta` and `--filter` flags.

### `--meta` — simple key=value filters

Repeatable flag for equality filters on frontmatter fields.

```bash
# Single filter
bm tool search "my query" --meta status=draft

# Multiple filters (AND logic)
bm tool search "" --meta status=active --meta priority=high
```

### `--filter` — advanced JSON filters

Pass a full JSON filter dictionary for operator-based queries.

```bash
# Range filter
bm tool search "" --filter '{"score": {"$between": [0.3, 0.8]}}'

# $in filter
bm tool search "" --filter '{"priority": {"$in": ["high", "critical"]}}'
```

### `--tag` and `--status` — convenience shortcuts

```bash
bm tool search "query" --tag security --tag oauth
bm tool search "" --status draft
```

### Combined example

```bash
bm tool search "authentication" --tag security --meta status=draft --type spec
```

## Practical Examples

### Example notes with custom frontmatter

**`specs/auth-design.md`:**

```markdown
---
title: Auth Design
type: spec
tags: [security, oauth]
status: in-progress
priority: high
confidence: 0.85
---

# Auth Design

## Observations
- [decision] Use OAuth 2.1 with PKCE for all client types #security
- [requirement] Token refresh must be transparent to the user

## Relations
- implements [[Security Requirements]]
```

**`specs/search-redesign.md`:**

```markdown
---
title: Search Redesign
type: spec
tags: [search, performance]
status: draft
priority: medium
confidence: 0.6
---

# Search Redesign

## Observations
- [goal] Sub-100ms search response times #performance
- [approach] Hybrid FTS + vector retrieval

## Relations
- depends_on [[Database Schema]]
```

### Queries that find them

```python
# Find all in-progress specs
await bm_search(filters={"status": "in-progress", "type": "spec"})
# → Auth Design

# Find high-confidence specs
await bm_search(filters={"confidence": {"$gt": 0.7}})
# → Auth Design (confidence: 0.85)

# Find specs with priority high or medium
await bm_search(filters={"priority": {"$in": ["high", "medium"]}})
# → Auth Design, Search Redesign

# Find specs in a confidence range
await bm_search(filters={"confidence": {"$between": [0.5, 0.9]}})
# → Auth Design (0.85), Search Redesign (0.6)

# Find notes tagged with security
await bm_search("tag:security")
# → Auth Design

# Combined: text search + metadata filter
await bm_search("OAuth", filters={"status": "in-progress"})
# → Auth Design
```

### CLI equivalents

```bash
bm tool search "" --meta status=in-progress --type spec
bm tool search "" --filter '{"confidence": {"$gt": 0.7}}'
bm tool search "OAuth" --meta status=in-progress
bm tool search --tag security
```
