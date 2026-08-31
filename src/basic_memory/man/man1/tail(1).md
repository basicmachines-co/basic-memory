---
title: tail(1)
type: manpage
section: 1
name: tail
summary: show recently changed notes
generated: hand
---

# tail(1)

## NAME

**tail** — show recently changed notes

## SYNOPSIS

```
bm tail [--timeframe WINDOW] [-n N] [--json | --plain]
        [--project NAME | --project-id UUID] [--local | --cloud]
```

## DESCRIPTION

Shows the most recently changed notes in a project, newest first — tail as
in "the tail of the change log", not of one file. Each row carries the
creation time, type, title, permalink, and file path. On a TTY rows render
as a table; `--plain` prints tab-separated lines; `--json` (or piped
output) emits the rows as a JSON array.

## OPTIONS

- **--timeframe** — time window, e.g. `7d`, `yesterday`, `2 days ago`
  (default `7d`)
- **-n, --lines** — rows to show, 1-100 (default 10)

## EXAMPLES

```
bm tail
bm tail -n 20 --timeframe 1d
bm tail --plain | cut -f3
```

## SEE ALSO

- see_also [[head(1)]]
- see_also [[recent-activity(3)]]
