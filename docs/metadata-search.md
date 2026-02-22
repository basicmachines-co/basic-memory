# Metadata Search Reference

Basic Memory automatically indexes custom frontmatter fields so you can query them with structured filters. Any YAML key in a note's frontmatter beyond the standard set (`title`, `type`, `tags`, `permalink`, `schema`) is stored as `entity_metadata` and becomes searchable.

## Two Ways to Query

| Tool | Use When |
|------|----------|
| `search_by_metadata` | You only need metadata filters (no text query) |
| `search_notes` | You want to combine a text query with metadata filters |

Both tools accept the same filter syntax.

## Filter Syntax

Filters are a JSON dictionary where each key targets a frontmatter field and the value specifies the match condition. Multiple keys combine with **AND** logic — every filter must match.

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
| Array contains (all) | `{"field": ["a", "b"]}` | `{"tags": ["security", "oauth"]}` |
| `$in` (any of) | `{"field": {"$in": [...]}}` | `{"priority": {"$in": ["high", "critical"]}}` |
| `$gt` / `$gte` | `{"field": {"$gt": N}}` | `{"confidence": {"$gt": 0.7}}` |
| `$lt` / `$lte` | `{"field": {"$lt": N}}` | `{"score": {"$lt": 0.5}}` |
| `$between` | `{"field": {"$between": [min, max]}}` | `{"score": {"$between": [0.3, 0.8]}}` |
| Nested access | `{"a.b": "value"}` | `{"schema.version": "2"}` |

**Key rules:**
- Filter keys must match `[A-Za-z0-9_-]+` (dots separate nesting levels).
- Each operator dict must contain exactly one operator.
- `$in` and array-contains require non-empty lists.
- `$between` requires exactly two values `[min, max]`.

## MCP Tools

### `search_by_metadata` — metadata-only search

Searches entities by structured frontmatter metadata without a text query. Results are scoped to entity-level items.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filters` | dict | Yes | Metadata filter dictionary (see syntax above) |
| `project` | string | No | Project to search in (uses default if omitted) |
| `limit` | int | No | Max results (default 20) |
| `offset` | int | No | Skip N results for pagination (default 0) |

**Example:**

```python
# Find all notes with status "in-progress"
await search_by_metadata({"status": "in-progress"})

# Find high-priority specs in the research project
await search_by_metadata(
    {"type": "spec", "priority": {"$in": ["high", "critical"]}},
    project="research",
    limit=10,
)
```

### `search_notes` with metadata — combined text + metadata

The `search_notes` tool accepts `metadata_filters`, `tags`, and `status` parameters alongside the text `query`. This lets you combine full-text search with structured filtering.

**Relevant parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Text search query (can be empty when using only filters) |
| `metadata_filters` | dict | Structured filter dict (same syntax as `search_by_metadata`) |
| `tags` | list[str] | Convenience shorthand — merged into `metadata_filters["tags"]` |
| `status` | string | Convenience shorthand — merged into `metadata_filters["status"]` |

**Merging rules:** `tags` and `status` are convenience shortcuts. They are merged into `metadata_filters` using `setdefault` — if the same key already exists in `metadata_filters`, the explicit filter wins.

**Examples:**

```python
# Text search filtered by metadata
await search_notes("authentication", metadata_filters={"status": "draft"})

# Filter-only search (empty query)
await search_notes("", metadata_filters={"type": "spec"})

# Combine text, tags shortcut, and metadata
await search_notes(
    "oauth flow",
    tags=["security"],
    metadata_filters={"confidence": {"$gt": 0.7}},
)

# Convenience shortcuts
await search_notes("planning", status="active")
await search_notes("", tags=["tier1", "alpha"])
```

## Tag Search Shortcuts

The `tag:` prefix in a search query is a shorthand for tag-based metadata filtering. When `search_notes` receives a query starting with `tag:`, it converts the query into a `tags` filter and clears the text query.

```python
# These are equivalent:
await search_notes("tag:tier1")
await search_notes("", tags=["tier1"])

# Multiple tags (comma or space separated) — all must be present:
await search_notes("tag:tier1,alpha")
await search_notes("tag:tier1 alpha")
```

## CLI Access

The `bm tool search-notes` command exposes metadata filtering via `--meta` and `--filter` flags.

### `--meta` — simple key=value filters

Repeatable flag for equality filters on frontmatter fields.

```bash
# Single filter
bm tool search-notes "my query" --meta status=draft

# Multiple filters (AND logic)
bm tool search-notes "" --meta status=active --meta priority=high
```

### `--filter` — advanced JSON filters

Pass a full JSON filter dictionary for operator-based queries.

```bash
# Range filter
bm tool search-notes "" --filter '{"score": {"$between": [0.3, 0.8]}}'

# $in filter
bm tool search-notes "" --filter '{"priority": {"$in": ["high", "critical"]}}'
```

### `--tag` and `--status` — convenience shortcuts

```bash
bm tool search-notes "query" --tag security --tag oauth
bm tool search-notes "" --status draft
```

### Combined example

```bash
bm tool search-notes "authentication" --tag security --meta status=draft --type spec
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
await search_by_metadata({"status": "in-progress", "type": "spec"})
# → Auth Design

# Find high-confidence specs
await search_by_metadata({"confidence": {"$gt": 0.7}})
# → Auth Design (confidence: 0.85)

# Find specs with priority high or medium
await search_by_metadata({"priority": {"$in": ["high", "medium"]}})
# → Auth Design, Search Redesign

# Find specs in a confidence range
await search_by_metadata({"confidence": {"$between": [0.5, 0.9]}})
# → Auth Design (0.85), Search Redesign (0.6)

# Find notes tagged with security
await search_notes("tag:security")
# → Auth Design

# Combined: text search + metadata filter
await search_notes("OAuth", metadata_filters={"status": "in-progress"})
# → Auth Design
```

### CLI equivalents

```bash
bm tool search-notes "" --meta status=in-progress --type spec
bm tool search-notes "" --filter '{"confidence": {"$gt": 0.7}}'
bm tool search-notes "OAuth" --meta status=in-progress
bm tool search-notes --tag security
```
