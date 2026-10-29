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
        [--page-size PAGE_SIZE] [--project PROJECT] [--json]
        [--local | --cloud]
```

## DESCRIPTION

Shows the hierarchy under a directory (default: the project root), rebuilt
from the recursive `bm_find` listing `bm find` prints. Directories print with
a trailing slash. `--json` emits `bm_find`'s flat listing payload — the
hierarchy is a display concern.

## OPTIONS

- **--name** — File-name glob, e.g. "*.md"
- **--depth** — Folder levels to show (1-10)
- **--page** — Page number (1-indexed)
- **--page-size** (default: 100) — Entries per page (1-100)
- **--project** — Project name, 'workspace/project', or project id. Defaults to the default project.
- **--json** — Print the tool's JSON result
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm tree
bm tree /specs --depth 2
bm tree --name "*.md"
```

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[find(1)]]
