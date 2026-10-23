---
title: apropos(1)
type: manpage
section: 1
name: apropos
summary: search the Basic Memory manual
generated: cli
---

# apropos(1)

## NAME

**apropos** — search the Basic Memory manual

## SYNOPSIS

```
bm man apropos QUERY [--json]
```

## DESCRIPTION

Lists the bundled manual pages that mention every word of the query, in any
case, like `man -k`. Pages whose name, tool or summary carry the words come
first. It reads the pages shipped with the package, so it needs no project or
network; bm_man(query=...) runs the same search from an MCP client.

An empty result is a successful search (exit 0), not an error.

## OPTIONS

- **--json** — Output raw JSON instead of formatted display

## EXAMPLES

```
bm man apropos "frontmatter filters"
# → apropos(1)                   search the Basic Memory manual
#   bm-find(3)                   find files by name or notes by frontmatter, recursively
#   bm-man(3)                    read the Basic Memory manual
#   bm-search(3)                 search a project's notes, ranked by relevance
#   search-notes(3)              search the knowledge base by text, similarity, or metadata
bm man apropos checksum --json
bm man list            # index of the bundled pages
bm man bm_search       # read one page
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[bm-man(3)]]
