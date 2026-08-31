---
title: tree(1)
type: manpage
section: 1
name: tree
summary: show a directory hierarchy
generated: hand
---

# tree(1)

## NAME

**tree** — show a directory hierarchy

## SYNOPSIS

```
bm tree [PATH] [--name GLOB] [--depth N] [--page N] [--page-size N]
        [--json | --plain] [--project NAME | --project-id UUID]
        [--local | --cloud]
```

## DESCRIPTION

Shows the hierarchy under a directory (default: the project root), rebuilt
from the same recursive listing `bm find` uses. Directories print with a
trailing slash. On a TTY the hierarchy renders as a tree; `--plain` prints
two-space-indented lines; `--json` (or piped output) emits find's flat
listing payload — the hierarchy is a display concern.

## OPTIONS

- **--name** — file-name glob, e.g. `"*.md"`
- **--depth** — recursion depth, 1-10 (default 10)
- **--page, --page-size** — node pagination; a truncated page notes that
  more entries exist

## EXAMPLES

```
bm tree
bm tree /specs --depth 2
bm tree --name "*.md" --plain
```

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[find(1)]]
