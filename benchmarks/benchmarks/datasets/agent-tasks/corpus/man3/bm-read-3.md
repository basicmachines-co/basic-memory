---
title: bm-read(3)
type: manpage
section: 3
name: bm-read
summary: read a note by title, permalink, or memory:// URL
---

# bm-read(3)

## NAME

**bm-read** — read a note by title, permalink, or memory:// URL.

## SYNOPSIS

```
bm_read(identifier, project=None, start_line=None, end_line=None)
```

## DESCRIPTION

Returns the full Markdown of one note, including frontmatter by default.
The identifier resolves exactly; a missing or ambiguous note is a tool error.
For in-place changes use bm-edit(3); for discovery use bm-search(3).

## EXAMPLES

```
bm_read("specs/spec-9-search-rework")
```

## Relations

- see_also [[bm-edit(3)]]
- see_also [[bm-search(3)]]
