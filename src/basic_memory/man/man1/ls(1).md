---
title: ls(1)
type: manpage
section: 1
name: ls
summary: list one directory level of a project
generated: cli
---

# ls(1)

## NAME

**ls** — list one directory level of a project

## SYNOPSIS

```
bm ls [PATH] [--page PAGE] [--page-size PAGE_SIZE] [--json | --plain]
      [--project PROJECT] [--project-id PROJECT_ID] [--local | --cloud]
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
bm ls
bm ls /specs
bm ls /notes --plain
```

## SEE ALSO

- see_also [[find(1)]]
- see_also [[tree(1)]]
- see_also [[list-directory(3)]]
