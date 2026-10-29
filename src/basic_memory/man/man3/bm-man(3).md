---
title: bm-man(3)
type: manpage
section: 3
name: bm-man
summary: read the Basic Memory manual
generated: registry
tool: bm_man
verified: 0.24.1 mcp
---

# bm-man(3)

## NAME

**bm-man** — read the Basic Memory manual

## SYNOPSIS

```
bm_man(page=None, query=None, output_format="text")
```

## DESCRIPTION

Reads the manual bundled with Basic Memory, so it works with no project and no
network. With no arguments, the index of every page. `page` reads one page:
`bm-search(3)` for an MCP tool, `grep(1)` for a shell command; a tool name such
as `bm_search` works too. `query` lists the pages that mention every word given,
like `man -k`; pages whose name or summary match come first.

Section 3 documents the MCP tools, section 1 the `bm` shell commands, and
section 7 the concepts. The index marks pages for tools this server does not
register.

The shell counterparts are `bm man` and apropos(1).

## PARAMETERS

- **page** (string | null, optional, default: None) — The page to read, e.g. 'bm-search(3)', 'grep(1)' or 'bm_search'.
- **query** (string | null, optional, default: None) — Words to find in the manual; lists the pages that mention them all.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_man(page="bm_search")
# → # bm-search(3)
#   ## NAME
#   **bm-search** — search a project's notes, ranked by relevance
#   ...

bm_man(query="frontmatter filters")
# → Manual pages for 'frontmatter filters':
#   - apropos(1) — search the Basic Memory manual
#   - bm-agent(3) — run Basic Memory agents, follow their runs, and review what they changed (hosted server only)
#   - bm-find(3) — find files by name or notes by frontmatter, recursively
#   - bm-man(3) — read the Basic Memory manual
#   - bm-search(3) — search a project's notes, ranked by relevance

bm_man(page="nope(3)")
# → error: NOT_FOUND: No manual page 'nope(3)'.
#   Try instead: bm_man(query='nope(3)'), or bm_man() for the index
```

## GOTCHAS

- `topic`, `name`, `command` and `tool` are accepted as spellings of `page`.

## SEE ALSO

- see_also [[apropos(1)]]
- see_also [[tools-migration(7)]]
