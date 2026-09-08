---
title: tail(1)
type: manpage
section: 1
name: tail
summary: show recently changed notes
generated: cli
---

# tail(1)

## NAME

**tail** — show recently changed notes

## SYNOPSIS

```
bm tail [--timeframe TIMEFRAME] [--lines N] [--json] [--plain]
        [--project PROJECT] [--project-id PROJECT_ID] [--local] [--cloud]
```

## DESCRIPTION

Shows the most recently changed notes in a project, newest first — tail as
in "the tail of the change log", not of one file. Each row carries the
creation time, type, title, permalink, and file path. On a TTY rows render
as a table; `--plain` prints tab-separated lines; `--json` (or piped
output) emits the rows as a JSON array.

## OPTIONS

- **--timeframe** (default: "7d") — Time window, e.g. "7d", "yesterday"
- **-n, --lines** (default: 10) — Rows to show (1-100)
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--project** — The project to use. If not provided, the default project will be used.
- **--project-id** — Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm tail
bm tail -n 20 --timeframe 1d
bm tail --plain | cut -f3
```

## SEE ALSO

- see_also [[head(1)]]
- see_also [[recent-activity(3)]]
