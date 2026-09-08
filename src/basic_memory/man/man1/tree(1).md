---
title: tree(1)
type: manpage
section: 1
name: tree
summary: show a directory hierarchy
generated: cli
---

# tree(1)

## NAME

**tree** — show a directory hierarchy

## SYNOPSIS

```
bm tree [PATH] [--name NAME] [--depth DEPTH] [--page PAGE]
        [--page-size PAGE_SIZE] [--json | --plain] [--project PROJECT]
        [--project-id PROJECT_ID] [--local | --cloud]
```

## DESCRIPTION

Shows the hierarchy under a directory (default: the project root), rebuilt
from the same recursive listing `bm find` uses. Directories print with a
trailing slash. On a TTY the hierarchy renders as a tree; `--plain` prints
two-space-indented lines; `--json` (or piped output) emits find's flat
listing payload — the hierarchy is a display concern.

## OPTIONS

- **--name** — File-name glob, e.g. "*.md"
- **--depth** (default: 10) — Recursion depth (API bound 1-10)
- **--page** (default: 1) — Page number (1-indexed)
- **--page-size** (default: 10) — Nodes per page
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--project** — The project to use. If not provided, the default project will be used.
- **--project-id** — Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm tree
bm tree /specs --depth 2
bm tree --name "*.md" --plain
```

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[find(1)]]
