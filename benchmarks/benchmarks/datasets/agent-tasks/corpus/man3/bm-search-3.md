---
title: bm-search(3)
type: manpage
section: 3
name: bm-search
summary: full-text and metadata search across the knowledge base
---

# bm-search(3)

## NAME

**bm-search** — full-text and metadata search across the knowledge base.

## SYNOPSIS

```
bm_search(query, project=None, page=1, page_size=10,
             mode="text", filters=None, since=None)
```

## DESCRIPTION

Searches note titles, content, observations, and frontmatter metadata.
`filters` accepts equality, `$in`, `$gt`/`$gte`/`$lt`/`$lte`,
`$between`, and dot-notation nested keys. Whole-note reads belong to
bm-read(3); this page only finds them.

## EXAMPLES

```
bm_search(query="cache", filters={"status": "active"})
```

## Relations

- see_also [[bm-edit(3)]]
- see_also [[bm-read(3)]]
