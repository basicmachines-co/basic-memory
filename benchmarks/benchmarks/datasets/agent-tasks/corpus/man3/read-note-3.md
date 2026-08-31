---
title: read-note(3)
type: manpage
section: 3
name: read-note
summary: read a note by title, permalink, or memory:// URL
---

# read-note(3)

## NAME

**read-note** — read a note by title, permalink, or memory:// URL.

## SYNOPSIS

```
read_note(identifier, project=None, page=1, page_size=10)
```

## DESCRIPTION

Returns the full markdown of one note with knowledge-graph context. The
identifier resolves fuzzily: an exact permalink wins, then title match.
For in-place changes use edit-note(3); for discovery use search-notes(3).

## EXAMPLES

```
read_note("specs/spec-9-search-rework")
```

## Relations

- see_also [[edit-note(3)]]
- see_also [[search-notes(3)]]
