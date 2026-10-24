---
name: memory-metadata-search
description: "Search Basic Memory notes by custom frontmatter fields with equality, range, array, and nested filters. Use when finding notes by status, priority, confidence, or any YAML field, not free text."
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

Use `bm_search` for ranked results or `bm_find` for file discovery with selected fields. Pass frontmatter conditions via `filters`, including tags and status. Omit `query` (or pass `None`) for filter-only searches.

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

### Null (field missing or explicitly null)

```json
{"owner": null}
```

Matches notes with no `owner` key and notes whose `owner` is explicitly null.
Null works only as a plain equality value — inside `$in`, `$between`, an
array-contains list, or a comparison it is rejected, because those compare
against the value and a comparison with null is never true.

### Nested Access (dot notation)

```json
{"schema.version": "2"}
```

### Quick Reference

| Operator | Syntax | Example |
|----------|--------|---------|
| Equality | `{"field": "value"}` | `{"status": "active"}` |
| Is null | `{"field": null}` | `{"owner": null}` |
| Array contains | `{"field": ["a", "b"]}` | `{"tags": ["security", "oauth"]}` |
| `$in` | `{"field": {"$in": [...]}}` | `{"priority": {"$in": ["high", "critical"]}}` |
| `$gt` / `$gte` | `{"field": {"$gt": N}}` | `{"confidence": {"$gt": 0.7}}` |
| `$lt` / `$lte` | `{"field": {"$lt": N}}` | `{"score": {"$lt": 0.5}}` |
| `$between` | `{"field": {"$between": [lo, hi]}}` | `{"score": {"$between": [0.3, 0.8]}}` |
| `$contains` | `{"field": {"$contains": value}}` | `{"tags": {"$contains": "security"}}` |
| `$exists` | `{"field": {"$exists": true}}` | `{"owner": {"$exists": false}}` |
| Nested | `{"a.b": "value"}` | `{"schema.version": "2"}` |

**Rules:**
- Keys must match `[A-Za-z0-9_-]+` (dots separate nesting levels)
- Several operators on one field AND together: `{"started": {"$gte": "2026-01-01", "$lt": "2026-02-01"}}`
- `$in` and array-contains require non-empty lists
- `$between` requires exactly `[min, max]`
- `null` is an is-null match and only valid as a plain equality value
- Comparison and `$between` bounds must be finite numbers — a magnitude no float
  can hold (a 400-digit integer, which JSON keeps as an ordinary `int`) is
  refused rather than compared against an infinite bound
- Metadata filters match Markdown notes only — indexed PDFs, images and other
  regular files carry no frontmatter and are never hits, not even for `null`

> The leading `$` is optional (`gte` works), but write it for clarity.

Frontmatter fields say when a note was filed or reviewed. To ask what a note
*claims was true* on a date, use observation qualifiers (see **memory-notes**)
and `bm_search(valid_at="2026-07-01")` or
`bm_search(valid_overlaps="[2026-07-01,2026-08-01)")`.

## Using `bm_search` with Metadata

Pass `filters` to `bm_search`. Omit `query` for filter-only searches, or combine text and filters together.

```python
# Filter-only — find all notes with a given status
bm_search(filters={"status": "in-progress"})

# Filter-only — high-priority specs in a specific project
bm_search(
    filters={"type": "spec", "priority": {"$in": ["high", "critical"]}},
    project="research",
    page_size=10,
)

# Filter-only — notes with confidence above a threshold
bm_search(filters={"confidence": {"$gt": 0.7}})

# Tags and status use the same filter syntax
bm_search(filters={"status": "active"})
bm_search(filters={"tags": ["security", "oauth"]})

# Text search narrowed by metadata
bm_search("authentication", filters={"status": "draft"})

# Mix text, tags, and an advanced filter
bm_search(
    "oauth flow",
    filters={"tags": ["security"], "confidence": {"$gt": 0.7}},
)
```

Use one `filters` object for all frontmatter conditions; its keys combine with AND.

## Tag Search Shorthand

The `tag:` prefix in a query converts to a tag filter automatically:

```python
# These are equivalent:
bm_search("tag:tier1")
bm_search(filters={"tags": ["tier1"]})

# Multiple tags (comma or space separated) — all must match:
bm_search("tag:tier1,alpha")
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
bm_search(filters={"status": "in-progress", "type": "spec"})

# By numeric threshold
bm_search(filters={"confidence": {"$gt": 0.7}})

# By priority set
bm_search(filters={"priority": {"$in": ["high", "critical"]}})

# By tag shorthand
bm_search("tag:security")

# Combined text + metadata
bm_search("OAuth", filters={"status": "in-progress"})
```

## Guidelines

- **Use metadata search for structured queries.** If you're looking for notes by a known field value (status, priority, type), metadata filters are more precise than text search.
- **Use text search for content queries.** If you're looking for notes *about* something, text search is better. Combine both when you need precision.
- **Custom fields are free.** Any YAML key you put in frontmatter becomes queryable — no schema or configuration required.
- **Multiple filters are AND.** `{"status": "active", "priority": "high"}` requires both conditions.
- **Omit `query` for filter-only searches.** `bm_search(filters={"status": "active"})` works without a text query.
- **Dot notation for nesting.** Access nested YAML structures with dots: `{"schema.version": "2"}` queries the `version` key inside a `schema` object.
- **Use the same filters for file discovery.** `bm_find(filters={"status": "active"}, fields=["title", "priority"])` returns selected frontmatter values without reading each note.
