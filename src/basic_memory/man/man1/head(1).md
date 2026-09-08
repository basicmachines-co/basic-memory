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
bm head IDENTIFIER [--lines N] [--frontmatter | --no-frontmatter] [--json]
        [--plain] [--project PROJECT] [--project-id PROJECT_ID] [--local]
        [--cloud]
```

## DESCRIPTION

Prints the first `-n` lines (default 10) of one note, resolved exactly by
title, permalink, or memory:// URL. head is `bm cat` with a fixed line
range, so its JSON payload is exactly cat's: content plus start_line,
end_line, and total_lines.

## OPTIONS

- **-n, --lines** (default: 10) — Number of lines to print (from line 1)
- **--frontmatter / --no-frontmatter** (default: --frontmatter) — Include the YAML frontmatter block
- **--json** — Output raw JSON instead of formatted display
- **--plain** — Output undecorated plain text (no colors/markup), even when piped
- **--project** — The project to use. If not provided, the default project will be used.
- **--project-id** — Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects across cloud workspaces.
- **--local** — Force local API routing (ignore cloud mode)
- **--cloud** — Force cloud API routing

## EXAMPLES

```
bm head specs/search
bm head specs/search -n 3 --plain
```

## SEE ALSO

- see_also [[cat(1)]]
- see_also [[tail(1)]]
