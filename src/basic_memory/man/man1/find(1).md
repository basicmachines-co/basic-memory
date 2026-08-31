---
title: find(1)
type: manpage
section: 1
name: find
summary: recursively list files matching a name glob
generated: hand
---

# find(1)

## NAME

**find** — recursively list files matching a name glob

## SYNOPSIS

```
bm find [PATH] [--name GLOB] [--depth N] [--page N] [--page-size N]
        [--json | --plain] [--project NAME | --project-id UUID]
        [--local | --cloud]
```

## DESCRIPTION

Recursively lists files under a directory (default: the project root),
optionally filtered by a file-name glob. Depth is bounded 1-10 by the
directory API. On a TTY results render as a table; `--plain` prints one
path per line, find(1) style; `--json` (or piped output) emits the listing
with pagination and totals.

## OPTIONS

- **--name** — file-name glob, e.g. `"*.md"`; omitted matches everything
- **--depth** — recursion depth, 1-10 (default 10)
- **--page, --page-size** — node pagination (defaults 1 and 10)

## EXAMPLES

```
bm find --name "*.md"
bm find /specs --depth 3
bm find /notes --name "auth*" --plain
```

## SEE ALSO

- see_also [[ls(1)]]
- see_also [[tree(1)]]
- see_also [[list-directory(3)]]
