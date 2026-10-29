---
title: head(1)
type: manpage
section: 1
name: head
summary: print the first lines of a note
generated: cli
---

# head(1)

## NAME

**head** — print the first lines of a note

## SYNOPSIS

```
bm head IDENTIFIER [--lines N] [--project PROJECT] [--json]
        [--local | --cloud]
```

## DESCRIPTION

Prints the first `-n` lines (default 10) of one note through `bm_read`,
resolved by path, permalink, memory:// URL, or exact title. Lines count the
whole file, frontmatter included. The text result numbers the lines; `--json`
is `bm_read`'s slice payload: content plus start_line, end_line, total_lines,
and the next range to read.

## OPTIONS

- **-n, --lines** (default: 10) — Number of lines to print (from line 1)
- **--project** — Project name, 'workspace/project', or project id. Defaults to the default project.
- **--json** — Print the tool's JSON result
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm head specs/search
bm head specs/search -n 3
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[bm-read(3)]]
