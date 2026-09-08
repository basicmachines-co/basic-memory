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
bm man apropos QUERY [--project PROJECT] [--json] [--plain] [--local]
               [--cloud]
```

## DESCRIPTION

Searches manual pages stored as notes in the manual project (default:
`manual`), the same search the `man` MCP tool runs in query mode. The
bundled pages shipped with the package are indexed by `bm man list`
instead; a topic is read with `bm man <topic>`, which falls back to the
manual project when the topic is not bundled.

An empty result is a successful search (exit 0), not an error.

## OPTIONS

- **--project** — Manual project to search (default: manual)
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm man apropos "conflict resolution"
bm man apropos sync --json
bm man list            # index of the bundled pages
bm man cat             # read one page, bundled pages first
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[search-notes(3)]]
