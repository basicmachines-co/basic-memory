---
title: ls(1)
type: manpage
section: 1
name: ls
summary: list one directory level of a project
generated: hand
---

# ls(1)

## NAME

**ls** — list one directory level of a project

## SYNOPSIS

```
bm ls [PATH] [--page N] [--page-size N] [--json | --plain]
      [--project NAME | --project-id UUID] [--local | --cloud]
```

## DESCRIPTION

Lists the immediate contents of one directory (default: the project root).
Directories print with a trailing slash. On a TTY the listing renders as a
table with title, permalink, and update time; `--plain` prints one path per
line, ls -1 style; `--json` (or piped output) emits the listing with
pagination and totals.

`bm ls` lists files inside one project; the unrelated `bm project ls` lists
projects.

## OPTIONS

- **--page, --page-size** — node pagination (defaults 1 and 10)

## EXAMPLES

```
bm ls
bm ls /specs
bm ls /notes --plain
```

## SEE ALSO

- see_also [[find(1)]]
- see_also [[tree(1)]]
- see_also [[list-directory(3)]]
