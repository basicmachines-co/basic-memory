---
name: memory-metadata-search
description: "Structured metadata search for Basic Memory: query notes by custom frontmatter fields using equality, range, array, and nested filters. Use when finding notes by status, priority, confidence, or any custom YAML field rather than free-text content."
---

# Memory Metadata Search

Find notes by their structured frontmatter fields instead of (or in addition to) free-text content. Any custom YAML key in a note's frontmatter beyond the standard set (`title`, `type`, `tags`, `permalink`, `schema`) is automatically indexed as `entity_metadata` and becomes queryable.

## When to Use

- **Filtering by status or priority** — find all notes with `status: draft` or `priority: high`
- **Querying custom fields** — any frontmatter key you invent is searchable
- **Range queries** — find notes with `confidence > 0.7` or `score between 0.3 and 0.8`
- **Combining text + metadata** — narrow a text search with structured constraints
- **Tag-based filtering** — find notes tagged with specific frontmatter tags
- **Schema-aware queries** — filter by nested schema fields using dot notation

## The Tool

All metadata searching uses `search_notes`. Pass filters via `metadata_filters`, or use the `tags` and `status` convenience shortcuts. Omit `query` (or pass `None`) for filter-only searches.

## Filter Syntax

Filters are a JSON dictionary. Each key targets a frontmatter field; the value specifies the match condition. Multiple keys combine with **AND** logic.

### Equality

```json
{"status": "active"}
```

### Array Contains (all listed values must be present)

```json
{"tags": ["security", "oauth"]}
```

### `$in` (match any value in list)

```json
{"priority": {"$in": ["high", "critical"]}}
```

### Comparisons (`$gt`, `$gte`, `$lt`, `$lte`)

```json
{"confidence": {"$gt": 0.7}}
```

Numeric values use numeric comparison; strings use lexicographic comparison.

### `$between` (inclusive range)

```json
{"score": {"$between": [0.3, 0.8]}}
```

### Nested Access (dot notation)

```json
{"schema.version": "2"}
```

### Quick Reference

| Operator | Syntax | Example |
|----------|--------|---------|
| Equality | `{"field": "value"}` | `{"status": "active"}` |
| Array contains | `{"field": ["a", "b"]}` | `{"tags": ["security", "oauth"]}` |
| `$in` | `{"field": {"$in": [...]}}` | `{"priority": {"$in": ["high", "critical"]}}` |
| `$gt` / `$gte` | `{"field": {"$gt": N}}` | `{"confidence": {"$gt": 0.7}}` |
| `$lt` / `$lte` | `{"field": {"$lt": N}}` | `{"score": {"$lt": 0.5}}` |
| `$between` | `{"field": {"$between": [lo, hi]}}` | `{"score": {"$between": [0.3, 0.8]}}` |
| Nested | `{"a.b": "value"}` | `{"schema.version": "2"}` |

**Rules:**
- Keys must match `[A-Za-z0-9_-]+` (dots separate nesting levels)
- Operator dicts must contain exactly one operator
- `$in` and array-contains require non-empty lists
- `$between` requires exactly `[min, max]`

> **Warning:** Operators MUST include the `$` prefix — write `$gte`, not `gte`. Without the prefix the filter is treated as an exact-match key and will silently return no results. Correct: `{"confidence": {"$gte": 0.7}}`. Wrong: `{"confidence": {"gte": 0.7}}`.

## Using `search_notes` with Metadata

Pass `metadata_filters`, `tags`, or `status` to `search_notes`. Omit `query` for filter-only searches, or combine text and filters together.

```python
# Filter-only — find all notes with a given status
search_notes(metadata_filters={"status": "in-progress"})

# Filter-only — high-priority specs in a specific project
search_notes(
    metadata_filters={"type": "spec", "priority": {"$in": ["high", "critical"]}},
    project="research",
    page_size=10,
)

# Filter-only — notes with confidence above a threshold
search_notes(metadata_filters={"confidence": {"$gt": 0.7}})

# Convenience shortcuts for tags and status
search_notes(status="active")
search_notes(tags=["security", "oauth"])

# Text search narrowed by metadata
search_notes("authentication", metadata_filters={"status": "draft"})

# Mix text, tag shortcut, and advanced filter
search_notes(
    "oauth flow",
    tags=["security"],
    metadata_filters={"confidence": {"$gt": 0.7}},
)
```

**Merging rules:** `tags` and `status` are convenience shortcuts merged into `metadata_filters` via `setdefault`. If the same key exists in `metadata_filters`, the explicit filter wins.

## Tag Search Shorthand

The `tag:` prefix in a query converts to a tag filter automatically:

```python
# These are equivalent:
search_notes("tag:tier1")
search_notes("", tags=["tier1"])

# Multiple tags (comma or space separated) — all must match:
search_notes("tag:tier1,alpha")
```

## Example: Custom Frontmatter in Practice

A note with custom fields:

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

Queries that find it:

```python
# By status and type
search_notes(metadata_filters={"status": "in-progress", "type": "spec"})

# By numeric threshold
search_notes(metadata_filters={"confidence": {"$gt": 0.7}})

# By priority set
search_notes(metadata_filters={"priority": {"$in": ["high", "critical"]}})

# By tag shorthand
search_notes("tag:security")

# Combined text + metadata
search_notes("OAuth", metadata_filters={"status": "in-progress"})
```

## Guidelines

- **Use metadata search for structured queries.** If you're looking for notes by a known field value (status, priority, type), metadata filters are more precise than text search.
- **Use text search for content queries.** If you're looking for notes *about* something, text search is better. Combine both when you need precision.
- **Custom fields are free.** Any YAML key you put in frontmatter becomes queryable — no schema or configuration required.
- **Multiple filters are AND.** `{"status": "active", "priority": "high"}` requires both conditions.
- **Omit `query` for filter-only searches.** `search_notes(metadata_filters={"status": "active"})` works without a text query.
- **Dot notation for nesting.** Access nested YAML structures with dots: `{"schema.version": "2"}` queries the `version` key inside a `schema` object.
- **Tags shortcut is convenient but limited.** `tags` and `status` are sugar for common fields. For anything else, use `metadata_filters` directly.
