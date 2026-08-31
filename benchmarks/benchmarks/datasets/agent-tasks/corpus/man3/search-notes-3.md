---
title: search-notes(3)
type: manpage
section: 3
name: search-notes
summary: full-text and metadata search across the knowledge base
---

# search-notes(3)

## NAME

**search-notes** — full-text and metadata search across the knowledge base.

## SYNOPSIS

```
search_notes(query, project=None, page=1, page_size=10,
             search_type="text", metadata_filters=None, after_date=None)
```

## DESCRIPTION

Searches note titles, content, observations, and frontmatter metadata.
`metadata_filters` accepts equality, `$in`, `$gt`/`$gte`/`$lt`/`$lte`,
`$between`, and dot-notation nested keys. Whole-note reads belong to
read-note(3); this page only finds them.

## EXAMPLES

```
search_notes(query="cache", metadata_filters={"status": "active"})
```

## Relations

- see_also [[edit-note(3)]]
- see_also [[read-note(3)]]
